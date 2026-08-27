from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, Any, List
from decimal import Decimal, ROUND_HALF_UP
from app.services.cache import get_revenue_summary
from app.services.reservations import list_properties_for_tenant
from app.core.auth import authenticate_request as get_current_user

router = APIRouter()

@router.get("/dashboard/properties")
async def get_dashboard_properties(
    current_user: dict = Depends(get_current_user)
) -> Dict[str, List[Dict[str, Any]]]:

    tenant_id = getattr(current_user, "tenant_id", "default_tenant") or "default_tenant"

    properties = await list_properties_for_tenant(tenant_id)

    return {"data": properties}

@router.get("/dashboard/summary")
async def get_dashboard_summary(
    property_id: str,
    current_user: dict = Depends(get_current_user)
) -> Dict[str, Any]:
    
    tenant_id = getattr(current_user, "tenant_id", "default_tenant") or "default_tenant"
    
    revenue_data = await get_revenue_summary(property_id, tenant_id)
    
    rounded_total = Decimal(revenue_data['total']).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    total_revenue_float = float(rounded_total)
    
    return {
        "property_id": revenue_data['property_id'],
        "total_revenue": total_revenue_float,
        "currency": revenue_data['currency'],
        "reservations_count": revenue_data['count']
    }
