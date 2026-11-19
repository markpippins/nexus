import logging
from typing import Any, Callable, Dict, Tuple
from app.models import ServiceRequest, ServiceResponse

logger = logging.getLogger(__name__)

class Broker:
    def __init__(self):
        self.handlers: Dict[Tuple[str, str], Callable] = {}

    def register(self, service: str, operation: str, handler: Callable):
        self.handlers[(service, operation)] = handler
        logger.info(f"Registered handler for service='{service}', operation='{operation}'")

    async def submit(self, request: ServiceRequest) -> ServiceResponse:
        logger.info(f"Processing request: service='{request.service}', operation='{request.operation}', requestId='{request.requestId}'")
        
        key = (request.service, request.operation)
        handler = self.handlers.get(key)

        if not handler:
            logger.warning(f"No handler found for service='{request.service}', operation='{request.operation}'")
            return ServiceResponse.error(
                errors=[{"code": "not_found", "message": f"Operation not found: {request.service}.{request.operation}"}],
                request_id=request.requestId,
                service=request.service,
                operation=request.operation
            )

        try:
            # Call the handler. We assume handlers accept the params dict.
            # In a more advanced version, we could inspect the handler signature and bind arguments.
            result = await handler(request.params) if self._is_async(handler) else handler(request.params)
            
            return ServiceResponse.success(
                data=result,
                request_id=request.requestId,
                service=request.service,
                operation=request.operation
            )
        except Exception as e:
            logger.error(f"Error executing handler: {e}", exc_info=True)
            return ServiceResponse.error(
                errors=[{"code": "service_error", "message": str(e)}],
                request_id=request.requestId,
                service=request.service,
                operation=request.operation
            )

    def _is_async(self, func: Callable) -> bool:
        import inspect
        return inspect.iscoroutinefunction(func)
