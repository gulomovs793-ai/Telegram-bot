import json
import os
from typing import Any, Dict, Optional

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

META_API_VERSION = os.getenv("META_API_VERSION", "v26.0")
META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN", "")
META_AD_ACCOUNT_ID = os.getenv("META_AD_ACCOUNT_ID", "")
ADMIN_KEY = os.getenv("ADMIN_KEY", "")

app = FastAPI(title="Meta Ads AI Gateway", version="0.3.0")


def _auth(x_admin_key: Optional[str]) -> None:
    if not ADMIN_KEY:
        raise HTTPException(status_code=503, detail="ADMIN_KEY is not configured")
    if x_admin_key != ADMIN_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _require_meta() -> None:
    if not META_ACCESS_TOKEN:
        raise HTTPException(status_code=503, detail="META_ACCESS_TOKEN is not configured")
    if not META_AD_ACCOUNT_ID:
        raise HTTPException(status_code=503, detail="META_AD_ACCOUNT_ID is not configured")


def _account_id() -> str:
    return META_AD_ACCOUNT_ID if META_AD_ACCOUNT_ID.startswith("act_") else f"act_{META_AD_ACCOUNT_ID}"


def _live_guard(status: str, x_confirm_live: Optional[str]) -> None:
    if status.upper() == "ACTIVE" and x_confirm_live != "ACTIVATE":
        raise HTTPException(
            status_code=409,
            detail="Live activation blocked. Send X-Confirm-Live: ACTIVATE only after explicit approval.",
        )


async def meta_request(
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    _require_meta()
    url = f"https://graph.facebook.com/{META_API_VERSION}/{path.lstrip('/')}"
    query = dict(params or {})
    query["access_token"] = META_ACCESS_TOKEN
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.request(method, url, params=query, data=data)
    try:
        payload = response.json()
    except Exception:
        payload = {"raw": response.text}
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail={"meta_status": response.status_code, "meta": payload})
    return payload


@app.on_event("startup")
async def startup_meta_diagnostics() -> None:
    if not META_ACCESS_TOKEN or not META_AD_ACCOUNT_ID:
        print("META_DIAGNOSTIC configured=false")
        return
    base = f"https://graph.facebook.com/{META_API_VERSION}"
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            r = await client.get(f"{base}/me", params={"fields": "id", "access_token": META_ACCESS_TOKEN})
            print(f"META_DIAGNOSTIC token_status={r.status_code}")
        except Exception as exc:
            print(f"META_DIAGNOSTIC token_network_error={type(exc).__name__}")
        try:
            r = await client.get(
                f"{base}/{_account_id()}/campaigns",
                params={"fields": "id", "limit": 1, "access_token": META_ACCESS_TOKEN},
            )
            if r.status_code < 400:
                print("META_DIAGNOSTIC ad_account_access=ok")
            else:
                err = (r.json() or {}).get("error", {})
                print(
                    "META_DIAGNOSTIC ad_account_access=failed "
                    f"status={r.status_code} code={err.get('code')} subcode={err.get('error_subcode')}"
                )
        except Exception as exc:
            print(f"META_DIAGNOSTIC ad_account_network_error={type(exc).__name__}")


class CampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    objective: str = "OUTCOME_TRAFFIC"
    status: str = "PAUSED"
    special_ad_categories: list[str] = []
    is_adset_budget_sharing_enabled: bool = False


class StatusChange(BaseModel):
    status: str


class AdSetCreate(BaseModel):
    campaign_id: str
    name: str = Field(min_length=1, max_length=200)
    daily_budget: int = Field(default=500, ge=100)
    billing_event: str = "IMPRESSIONS"
    optimization_goal: str = "LINK_CLICKS"
    bid_strategy: str = "LOWEST_COST_WITHOUT_CAP"
    status: str = "PAUSED"
    age_min: int = Field(default=18, ge=13, le=65)
    age_max: int = Field(default=35, ge=13, le=65)
    countries: list[str] = ["UZ"]
    cities: list[dict[str, Any]] = []
    interests: list[dict[str, Any]] = []
    genders: list[int] = []
    publisher_platforms: list[str] = ["facebook", "instagram"]
    facebook_positions: list[str] = []
    instagram_positions: list[str] = []
    destination_type: Optional[str] = None
    promoted_object: Optional[dict[str, Any]] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None


class CreativeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    page_id: str
    instagram_actor_id: Optional[str] = None
    message: str = ""
    link: Optional[str] = None
    image_hash: Optional[str] = None
    video_id: Optional[str] = None
    headline: Optional[str] = None
    description: Optional[str] = None
    call_to_action_type: str = "LEARN_MORE"


class AdCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    adset_id: str
    creative_id: str
    status: str = "PAUSED"


class DraftBundle(BaseModel):
    campaign_name: str
    adset_name: str
    daily_budget: int = Field(default=500, ge=100)
    objective: str = "OUTCOME_TRAFFIC"
    optimization_goal: str = "LINK_CLICKS"
    age_min: int = 18
    age_max: int = 35
    countries: list[str] = ["UZ"]
    cities: list[dict[str, Any]] = []
    interests: list[dict[str, Any]] = []


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "meta_configured": bool(META_ACCESS_TOKEN and META_AD_ACCOUNT_ID),
        "admin_configured": bool(ADMIN_KEY),
        "api_version": META_API_VERSION,
        "safety": "paused-first",
    }


@app.get("/account")
async def account_info(x_admin_key: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _auth(x_admin_key)
    return await meta_request(
        "GET",
        _account_id(),
        params={"fields": "id,name,account_status,currency,timezone_name,amount_spent,balance,spend_cap"},
    )


@app.get("/campaigns")
async def list_campaigns(x_admin_key: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _auth(x_admin_key)
    return await meta_request(
        "GET",
        f"{_account_id()}/campaigns",
        params={"fields": "id,name,status,objective,effective_status,created_time,updated_time", "limit": 50},
    )


@app.post("/campaigns")
async def create_campaign(
    body: CampaignCreate,
    x_admin_key: Optional[str] = Header(default=None),
    x_confirm_live: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    _auth(x_admin_key)
    _live_guard(body.status, x_confirm_live)
    data = {
        "name": body.name,
        "objective": body.objective,
        "status": body.status.upper(),
        "special_ad_categories": json.dumps(body.special_ad_categories),
        "is_adset_budget_sharing_enabled": "true" if body.is_adset_budget_sharing_enabled else "false",
    }
    return await meta_request("POST", f"{_account_id()}/campaigns", data=data)


@app.post("/campaigns/{campaign_id}/status")
async def set_campaign_status(
    campaign_id: str,
    body: StatusChange,
    x_admin_key: Optional[str] = Header(default=None),
    x_confirm_live: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    _auth(x_admin_key)
    _live_guard(body.status, x_confirm_live)
    return await meta_request("POST", campaign_id, data={"status": body.status.upper()})


@app.get("/adsets")
async def list_adsets(x_admin_key: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _auth(x_admin_key)
    return await meta_request(
        "GET",
        f"{_account_id()}/adsets",
        params={"fields": "id,name,campaign_id,status,effective_status,daily_budget,lifetime_budget,targeting,optimization_goal", "limit": 50},
    )


@app.post("/adsets")
async def create_adset(
    body: AdSetCreate,
    x_admin_key: Optional[str] = Header(default=None),
    x_confirm_live: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    _auth(x_admin_key)
    _live_guard(body.status, x_confirm_live)
    if body.age_min > body.age_max:
        raise HTTPException(status_code=422, detail="age_min cannot exceed age_max")

    geo_locations: Dict[str, Any] = {}
    if body.countries:
        geo_locations["countries"] = body.countries
    if body.cities:
        geo_locations["cities"] = body.cities

    targeting: Dict[str, Any] = {
        "age_min": body.age_min,
        "age_max": body.age_max,
        "geo_locations": geo_locations,
        "publisher_platforms": body.publisher_platforms,
    }
    if body.genders:
        targeting["genders"] = body.genders
    if body.interests:
        targeting["flexible_spec"] = [{"interests": body.interests}]
    if body.facebook_positions:
        targeting["facebook_positions"] = body.facebook_positions
    if body.instagram_positions:
        targeting["instagram_positions"] = body.instagram_positions

    data: Dict[str, Any] = {
        "campaign_id": body.campaign_id,
        "name": body.name,
        "daily_budget": str(body.daily_budget),
        "billing_event": body.billing_event,
        "optimization_goal": body.optimization_goal,
        "bid_strategy": body.bid_strategy,
        "targeting": json.dumps(targeting),
        "status": body.status.upper(),
    }
    if body.destination_type:
        data["destination_type"] = body.destination_type
    if body.promoted_object:
        data["promoted_object"] = json.dumps(body.promoted_object)
    if body.start_time:
        data["start_time"] = body.start_time
    if body.end_time:
        data["end_time"] = body.end_time
    return await meta_request("POST", f"{_account_id()}/adsets", data=data)


@app.post("/adsets/{adset_id}/status")
async def set_adset_status(
    adset_id: str,
    body: StatusChange,
    x_admin_key: Optional[str] = Header(default=None),
    x_confirm_live: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    _auth(x_admin_key)
    _live_guard(body.status, x_confirm_live)
    return await meta_request("POST", adset_id, data={"status": body.status.upper()})


@app.get("/creatives")
async def list_creatives(x_admin_key: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _auth(x_admin_key)
    return await meta_request(
        "GET",
        f"{_account_id()}/adcreatives",
        params={"fields": "id,name,status,object_story_spec,thumbnail_url", "limit": 50},
    )


@app.post("/creatives")
async def create_creative(body: CreativeCreate, x_admin_key: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _auth(x_admin_key)
    story: Dict[str, Any] = {"page_id": body.page_id}
    if body.instagram_actor_id:
        story["instagram_actor_id"] = body.instagram_actor_id

    if body.video_id:
        video_data: Dict[str, Any] = {
            "video_id": body.video_id,
            "message": body.message,
            "call_to_action": {"type": body.call_to_action_type},
        }
        if body.link:
            video_data["call_to_action"]["value"] = {"link": body.link}
        if body.headline:
            video_data["title"] = body.headline
        story["video_data"] = video_data
    else:
        if not body.link:
            raise HTTPException(status_code=422, detail="link is required for image/link creative")
        link_data: Dict[str, Any] = {
            "link": body.link,
            "message": body.message,
            "call_to_action": {"type": body.call_to_action_type, "value": {"link": body.link}},
        }
        if body.image_hash:
            link_data["image_hash"] = body.image_hash
        if body.headline:
            link_data["name"] = body.headline
        if body.description:
            link_data["description"] = body.description
        story["link_data"] = link_data

    return await meta_request(
        "POST",
        f"{_account_id()}/adcreatives",
        data={"name": body.name, "object_story_spec": json.dumps(story)},
    )


@app.get("/ads")
async def list_ads(x_admin_key: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _auth(x_admin_key)
    return await meta_request(
        "GET",
        f"{_account_id()}/ads",
        params={"fields": "id,name,adset_id,campaign_id,status,effective_status,creative", "limit": 50},
    )


@app.post("/ads")
async def create_ad(
    body: AdCreate,
    x_admin_key: Optional[str] = Header(default=None),
    x_confirm_live: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    _auth(x_admin_key)
    _live_guard(body.status, x_confirm_live)
    return await meta_request(
        "POST",
        f"{_account_id()}/ads",
        data={
            "name": body.name,
            "adset_id": body.adset_id,
            "creative": json.dumps({"creative_id": body.creative_id}),
            "status": body.status.upper(),
        },
    )


@app.post("/ads/{ad_id}/status")
async def set_ad_status(
    ad_id: str,
    body: StatusChange,
    x_admin_key: Optional[str] = Header(default=None),
    x_confirm_live: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    _auth(x_admin_key)
    _live_guard(body.status, x_confirm_live)
    return await meta_request("POST", ad_id, data={"status": body.status.upper()})


@app.post("/drafts/campaign-adset")
async def create_campaign_adset_draft(body: DraftBundle, x_admin_key: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _auth(x_admin_key)
    campaign = await meta_request(
        "POST",
        f"{_account_id()}/campaigns",
        data={
            "name": body.campaign_name,
            "objective": body.objective,
            "status": "PAUSED",
            "special_ad_categories": "[]",
            "is_adset_budget_sharing_enabled": "false",
        },
    )
    campaign_id = campaign.get("id")
    if not campaign_id:
        raise HTTPException(status_code=502, detail="Campaign created without id")

    targeting: Dict[str, Any] = {
        "age_min": body.age_min,
        "age_max": body.age_max,
        "geo_locations": {"countries": body.countries},
        "publisher_platforms": ["facebook", "instagram"],
    }
    if body.cities:
        targeting["geo_locations"]["cities"] = body.cities
    if body.interests:
        targeting["flexible_spec"] = [{"interests": body.interests}]

    try:
        adset = await meta_request(
            "POST",
            f"{_account_id()}/adsets",
            data={
                "campaign_id": campaign_id,
                "name": body.adset_name,
                "daily_budget": str(body.daily_budget),
                "billing_event": "IMPRESSIONS",
                "optimization_goal": body.optimization_goal,
                "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
                "targeting": json.dumps(targeting),
                "status": "PAUSED",
            },
        )
    except HTTPException as exc:
        return {"campaign": campaign, "adset": None, "adset_error": exc.detail, "safe_status": "PAUSED"}

    return {"campaign": campaign, "adset": adset, "safe_status": "PAUSED"}


@app.get("/insights")
async def account_insights(
    date_preset: str = "last_7d",
    level: str = "account",
    x_admin_key: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    _auth(x_admin_key)
    return await meta_request(
        "GET",
        f"{_account_id()}/insights",
        params={
            "fields": "campaign_name,adset_name,ad_name,spend,impressions,reach,clicks,cpc,cpm,ctr,actions,cost_per_action_type",
            "date_preset": date_preset,
            "level": level,
        },
    )


@app.get("/targeting/search")
async def targeting_search(
    q: str,
    type: str = "adinterest",
    x_admin_key: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    _auth(x_admin_key)
    return await meta_request(
        "GET",
        "search",
        params={"type": "adTargetingCategory" if type == "category" else type, "q": q, "limit": 25},
    )


@app.get("/meta/me")
async def meta_me(x_admin_key: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _auth(x_admin_key)
    return await meta_request("GET", "me", params={"fields": "id,name"})
