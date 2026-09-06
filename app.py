import os
from typing import Any, Dict, Optional

import httpx
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel, Field

META_API_VERSION = os.getenv("META_API_VERSION", "v26.0")
META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN", "")
META_AD_ACCOUNT_ID = os.getenv("META_AD_ACCOUNT_ID", "")
ADMIN_KEY = os.getenv("ADMIN_KEY", "")

app = FastAPI(title="Meta Ads AI Gateway", version="0.1.0")


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


async def meta_request(method: str, path: str, *, params: Optional[Dict[str, Any]] = None, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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


class CampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    objective: str = "OUTCOME_TRAFFIC"
    status: str = "PAUSED"
    special_ad_categories: list[str] = []
    is_adset_budget_sharing_enabled: bool = False


class CampaignStatus(BaseModel):
    status: str


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "meta_configured": bool(META_ACCESS_TOKEN and META_AD_ACCOUNT_ID),
        "admin_configured": bool(ADMIN_KEY),
        "api_version": META_API_VERSION,
    }


@app.get("/campaigns")
async def list_campaigns(x_admin_key: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _auth(x_admin_key)
    return await meta_request(
        "GET",
        f"{_account_id()}/campaigns",
        params={"fields": "id,name,status,objective,effective_status,created_time,updated_time", "limit": 50},
    )


@app.post("/campaigns")
async def create_campaign(body: CampaignCreate, x_admin_key: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _auth(x_admin_key)
    data = {
        "name": body.name,
        "objective": body.objective,
        "status": body.status,
        "special_ad_categories": str(body.special_ad_categories).replace("'", '"'),
        "is_adset_budget_sharing_enabled": "true" if body.is_adset_budget_sharing_enabled else "false",
    }
    return await meta_request("POST", f"{_account_id()}/campaigns", data=data)


@app.post("/campaigns/{campaign_id}/status")
async def set_campaign_status(campaign_id: str, body: CampaignStatus, x_admin_key: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _auth(x_admin_key)
    return await meta_request("POST", campaign_id, data={"status": body.status})


@app.get("/insights")
async def account_insights(
    date_preset: str = "last_7d",
    x_admin_key: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    _auth(x_admin_key)
    return await meta_request(
        "GET",
        f"{_account_id()}/insights",
        params={
            "fields": "spend,impressions,reach,clicks,cpc,cpm,ctr,actions,cost_per_action_type",
            "date_preset": date_preset,
            "level": "account",
        },
    )


@app.get("/meta/me")
async def meta_me(x_admin_key: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _auth(x_admin_key)
    return await meta_request("GET", "me", params={"fields": "id,name"})
