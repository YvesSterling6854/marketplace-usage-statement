"""HTTP entry point for issuing marketplace usage statements."""

from fastapi import FastAPI, HTTPException

from usage_statement import InfraiClient, InfraiError, StatementReceipt, StatementRequest, issue_statement


app = FastAPI(title="Marketplace usage statements")


@app.post("/statements", response_model=StatementReceipt, status_code=201)
async def create_statement(request: StatementRequest) -> StatementReceipt:
    client = InfraiClient()
    try:
        return await issue_statement(request, client)
    except InfraiError as exc:
        status = exc.status_code if 400 <= exc.status_code < 500 else 502
        raise HTTPException(status_code=status, detail={"code": exc.code, **exc.detail}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        await client.close()
