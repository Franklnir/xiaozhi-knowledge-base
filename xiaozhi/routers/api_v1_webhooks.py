import logging
from fastapi import APIRouter, Request, HTTPException, status
from fastapi.responses import JSONResponse

from xiaozhi.marketplace.payments.xendit import XenditPaymentProvider
from xiaozhi.marketplace.payments.midtrans import MidtransPaymentProvider
from xiaozhi.marketplace.payments.simulator import SimulatorPaymentProvider
from xiaozhi.marketplace.deps import get_marketplace_repo

logger = logging.getLogger("xiaozhi.marketplace.webhooks")
router = APIRouter(prefix="/api/v1/webhooks", tags=["Marketplace Webhooks"])


@router.post("/xendit")
async def xendit_webhook(request: Request):
    headers = dict(request.headers)
    raw_body = await request.body()
    provider = XenditPaymentProvider()

    try:
        event = await provider.verify_webhook(headers, raw_body)
    except PermissionError as exc:
        logger.warning("Xendit webhook unauthorized: %s", exc)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))
    except Exception as exc:
        logger.error("Xendit webhook invalid payload: %s", exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    if event.status == "PAID":
        repo = get_marketplace_repo()
        success = repo.finalize_order_payment(
            order_number=event.order_number,
            provider="xendit",
            provider_event_id=event.event_id,
            provider_reference=event.order_number,
            payload=event.raw_payload,
        )
        if not success:
            logger.warning("Failed to finalize Xendit order %s", event.order_number)

    return JSONResponse(status_code=200, content={"received": True, "provider": "xendit"})


@router.post("/midtrans")
async def midtrans_webhook(request: Request):
    headers = dict(request.headers)
    raw_body = await request.body()
    provider = MidtransPaymentProvider()

    try:
        event = await provider.verify_webhook(headers, raw_body)
    except PermissionError as exc:
        logger.warning("Midtrans webhook unauthorized: %s", exc)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))
    except Exception as exc:
        logger.error("Midtrans webhook invalid payload: %s", exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    if event.status == "PAID":
        repo = get_marketplace_repo()
        success = repo.finalize_order_payment(
            order_number=event.order_number,
            provider="midtrans",
            provider_event_id=event.event_id,
            provider_reference=event.order_number,
            payload=event.raw_payload,
        )
        if not success:
            logger.warning("Failed to finalize Midtrans order %s", event.order_number)

    return JSONResponse(status_code=200, content={"received": True, "provider": "midtrans"})


@router.post("/simulator")
async def simulator_webhook(request: Request):
    headers = dict(request.headers)
    raw_body = await request.body()
    provider = SimulatorPaymentProvider()

    try:
        event = await provider.verify_webhook(headers, raw_body)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    if event.status == "PAID":
        repo = get_marketplace_repo()
        repo.finalize_order_payment(
            order_number=event.order_number,
            provider="simulator",
            provider_event_id=event.event_id,
            provider_reference=event.order_number,
            payload=event.raw_payload,
        )

    return JSONResponse(status_code=200, content={"received": True, "provider": "simulator"})
