import traceback
from fastapi.responses import JSONResponse
# from fastapi.exceptions import RequestValidationError

def debug_print(label: str, data=None):
    from core.settings import settings

    if not settings.debug:
        return

    print(f"\n[DEBUG] {label}")
    if data is not None:
        print(data)


def debug_error(e: Exception):
    from core.settings import settings

    if not settings.debug:
        return

    print("\n[DEBUG] Exception occurred:")
    print("Type:", type(e).__name__)
    print("Message:", str(e))
    print("Traceback:")
    print(traceback.format_exc())



async def validation_exception_handler(request, exc):
    # Raw body helps you see what the client actually sent
    try:
        body = await request.body()
        debug_print("Raw request body (before validation)", body.decode())
    except Exception:
        debug_print("Raw request body could not be read")

    # Full validation error
    safe_errors = exc.errors()
    debug_print("Validation errors", safe_errors)
    debug_error(exc)

    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()}
    )


# def debug_error(e: Exception):
#     return (
#         "<div style='padding:10px;background:#fee;border:1px solid #f00;color:#900;'>"
#         "<h3>Debug Error</h3>"
#         "<pre>"
#         f"{traceback.format_exc()}"
#         "</pre>"
#         "</div>"
#     )
