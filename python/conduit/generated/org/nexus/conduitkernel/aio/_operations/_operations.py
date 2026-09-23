# pylint: disable=too-many-lines
# coding=utf-8
from collections.abc import MutableMapping
from io import IOBase
import json
from typing import Any, Callable, IO, Optional, TypeVar, Union, overload

from corehttp.exceptions import (
    ClientAuthenticationError,
    HttpResponseError,
    ResourceExistsError,
    ResourceNotFoundError,
    ResourceNotModifiedError,
    StreamClosedError,
    StreamConsumedError,
    map_error,
)
from corehttp.rest import AsyncHttpResponse, HttpRequest
from corehttp.runtime import AsyncPipelineClient
from corehttp.runtime.pipeline import PipelineResponse
from corehttp.utils import case_insensitive_dict

from ... import models as _models
from ..._operations._operations import (
    build_conduitkernel_apply_delta_request,
    build_conduitkernel_check_consistency_request,
    build_conduitkernel_compare_replay_request,
    build_conduitkernel_delete_identity_request,
    build_conduitkernel_delete_receipts_request,
    build_conduitkernel_get_breaker_request,
    build_conduitkernel_get_delta_state_request,
    build_conduitkernel_get_failure_recovery_request,
    build_conduitkernel_get_graph_request,
    build_conduitkernel_get_identity_request,
    build_conduitkernel_get_latest_receipt_type_request,
    build_conduitkernel_get_lineage_request,
    build_conduitkernel_get_plan_detail_request,
    build_conduitkernel_get_plan_receipts_request,
    build_conduitkernel_get_receipt_request,
    build_conduitkernel_get_receipts_by_plan_request,
    build_conduitkernel_get_receipts_raw_request,
    build_conduitkernel_get_running_sessions_request,
    build_conduitkernel_get_session_request,
    build_conduitkernel_get_stale_sessions_request,
    build_conduitkernel_get_state_request,
    build_conduitkernel_insert_receipt_request,
    build_conduitkernel_kill_session_request,
    build_conduitkernel_list_identities_request,
    build_conduitkernel_list_replay_request,
    build_conduitkernel_list_sessions_request,
    build_conduitkernel_liveness_request,
    build_conduitkernel_metrics_request,
    build_conduitkernel_pause_breaker_request,
    build_conduitkernel_readiness_request,
    build_conduitkernel_reset_breaker_request,
    build_conduitkernel_resume_breaker_request,
    build_conduitkernel_root_request,
    build_conduitkernel_save_failure_recovery_request,
    build_conduitkernel_state_health_request,
    build_conduitkernel_trip_breaker_request,
    build_conduitkernel_update_identity_request,
    build_conduitkernel_update_session_cost_request,
    build_conduitkernel_update_session_heartbeat_request,
)
from ..._utils.model_base import SdkJSONEncoder, _deserialize
from ..._utils.utils import ClientMixinABC
from .._configuration import conduitkernelClientConfiguration

JSON = MutableMapping[str, Any]
T = TypeVar("T")
ClsType = Optional[Callable[[PipelineResponse[HttpRequest, AsyncHttpResponse], T, dict[str, Any]], Any]]


class _conduitkernelClientOperationsMixin(  # pylint: disable=too-many-public-methods
    ClientMixinABC[AsyncPipelineClient[HttpRequest, AsyncHttpResponse], conduitkernelClientConfiguration]
):

    async def metrics(self, **kwargs: Any) -> _models.MetricsResponse:
        """Prometheus metrics endpoint.

        :return: MetricsResponse. The MetricsResponse is compatible with MutableMapping
        :rtype: ~org.nexus.conduitkernel.models.MetricsResponse
        :raises ~corehttp.exceptions.HttpResponseError:
        """
        error_map: MutableMapping = {
            401: ClientAuthenticationError,
            404: ResourceNotFoundError,
            409: ResourceExistsError,
            304: ResourceNotModifiedError,
        }
        error_map.update(kwargs.pop("error_map", {}) or {})

        _headers = kwargs.pop("headers", {}) or {}
        _params = kwargs.pop("params", {}) or {}

        cls: ClsType[_models.MetricsResponse] = kwargs.pop("cls", None)

        _request = build_conduitkernel_metrics_request(
            headers=_headers,
            params=_params,
        )
        path_format_arguments = {
            "endpoint": self._serialize.url("self._config.endpoint", self._config.endpoint, "str", skip_quote=True),
        }
        _request.url = self._client.format_url(_request.url, **path_format_arguments)

        _decompress = kwargs.pop("decompress", True)
        _stream = kwargs.pop("stream", False)
        pipeline_response: PipelineResponse = await self._client.pipeline.run(  # type: ignore
            _request, stream=_stream, **kwargs
        )

        response = pipeline_response.http_response

        if response.status_code not in [200]:
            if _stream:
                try:
                    await response.read()  # Load the body in memory and close the socket
                except (StreamConsumedError, StreamClosedError):
                    pass
            map_error(status_code=response.status_code, response=response, error_map=error_map)
            raise HttpResponseError(response=response)

        if _stream:
            deserialized = response.iter_bytes() if _decompress else response.iter_raw()
        else:
            deserialized = _deserialize(_models.MetricsResponse, response.json())

        if cls:
            return cls(pipeline_response, deserialized, {})  # type: ignore

        return deserialized  # type: ignore

    async def liveness(self, **kwargs: Any) -> _models.LivenessResponse:
        """Liveness probe -- always returns 200 if process is running.

        :return: LivenessResponse. The LivenessResponse is compatible with MutableMapping
        :rtype: ~org.nexus.conduitkernel.models.LivenessResponse
        :raises ~corehttp.exceptions.HttpResponseError:
        """
        error_map: MutableMapping = {
            401: ClientAuthenticationError,
            404: ResourceNotFoundError,
            409: ResourceExistsError,
            304: ResourceNotModifiedError,
        }
        error_map.update(kwargs.pop("error_map", {}) or {})

        _headers = kwargs.pop("headers", {}) or {}
        _params = kwargs.pop("params", {}) or {}

        cls: ClsType[_models.LivenessResponse] = kwargs.pop("cls", None)

        _request = build_conduitkernel_liveness_request(
            headers=_headers,
            params=_params,
        )
        path_format_arguments = {
            "endpoint": self._serialize.url("self._config.endpoint", self._config.endpoint, "str", skip_quote=True),
        }
        _request.url = self._client.format_url(_request.url, **path_format_arguments)

        _decompress = kwargs.pop("decompress", True)
        _stream = kwargs.pop("stream", False)
        pipeline_response: PipelineResponse = await self._client.pipeline.run(  # type: ignore
            _request, stream=_stream, **kwargs
        )

        response = pipeline_response.http_response

        if response.status_code not in [200]:
            if _stream:
                try:
                    await response.read()  # Load the body in memory and close the socket
                except (StreamConsumedError, StreamClosedError):
                    pass
            map_error(status_code=response.status_code, response=response, error_map=error_map)
            raise HttpResponseError(response=response)

        if _stream:
            deserialized = response.iter_bytes() if _decompress else response.iter_raw()
        else:
            deserialized = _deserialize(_models.LivenessResponse, response.json())

        if cls:
            return cls(pipeline_response, deserialized, {})  # type: ignore

        return deserialized  # type: ignore

    async def readiness(self, **kwargs: Any) -> _models.ReadinessResponse:
        """Readiness probe -- checks DB connectivity and engine state.

        :return: ReadinessResponse. The ReadinessResponse is compatible with MutableMapping
        :rtype: ~org.nexus.conduitkernel.models.ReadinessResponse
        :raises ~corehttp.exceptions.HttpResponseError:
        """
        error_map: MutableMapping = {
            401: ClientAuthenticationError,
            404: ResourceNotFoundError,
            409: ResourceExistsError,
            304: ResourceNotModifiedError,
        }
        error_map.update(kwargs.pop("error_map", {}) or {})

        _headers = kwargs.pop("headers", {}) or {}
        _params = kwargs.pop("params", {}) or {}

        cls: ClsType[_models.ReadinessResponse] = kwargs.pop("cls", None)

        _request = build_conduitkernel_readiness_request(
            headers=_headers,
            params=_params,
        )
        path_format_arguments = {
            "endpoint": self._serialize.url("self._config.endpoint", self._config.endpoint, "str", skip_quote=True),
        }
        _request.url = self._client.format_url(_request.url, **path_format_arguments)

        _decompress = kwargs.pop("decompress", True)
        _stream = kwargs.pop("stream", False)
        pipeline_response: PipelineResponse = await self._client.pipeline.run(  # type: ignore
            _request, stream=_stream, **kwargs
        )

        response = pipeline_response.http_response

        if response.status_code not in [200]:
            if _stream:
                try:
                    await response.read()  # Load the body in memory and close the socket
                except (StreamConsumedError, StreamClosedError):
                    pass
            map_error(status_code=response.status_code, response=response, error_map=error_map)
            raise HttpResponseError(response=response)

        if _stream:
            deserialized = response.iter_bytes() if _decompress else response.iter_raw()
        else:
            deserialized = _deserialize(_models.ReadinessResponse, response.json())

        if cls:
            return cls(pipeline_response, deserialized, {})  # type: ignore

        return deserialized  # type: ignore

    async def root(self, **kwargs: Any) -> _models.RootResponse:
        """Service root.

        :return: RootResponse. The RootResponse is compatible with MutableMapping
        :rtype: ~org.nexus.conduitkernel.models.RootResponse
        :raises ~corehttp.exceptions.HttpResponseError:
        """
        error_map: MutableMapping = {
            401: ClientAuthenticationError,
            404: ResourceNotFoundError,
            409: ResourceExistsError,
            304: ResourceNotModifiedError,
        }
        error_map.update(kwargs.pop("error_map", {}) or {})

        _headers = kwargs.pop("headers", {}) or {}
        _params = kwargs.pop("params", {}) or {}

        cls: ClsType[_models.RootResponse] = kwargs.pop("cls", None)

        _request = build_conduitkernel_root_request(
            headers=_headers,
            params=_params,
        )
        path_format_arguments = {
            "endpoint": self._serialize.url("self._config.endpoint", self._config.endpoint, "str", skip_quote=True),
        }
        _request.url = self._client.format_url(_request.url, **path_format_arguments)

        _decompress = kwargs.pop("decompress", True)
        _stream = kwargs.pop("stream", False)
        pipeline_response: PipelineResponse = await self._client.pipeline.run(  # type: ignore
            _request, stream=_stream, **kwargs
        )

        response = pipeline_response.http_response

        if response.status_code not in [200]:
            if _stream:
                try:
                    await response.read()  # Load the body in memory and close the socket
                except (StreamConsumedError, StreamClosedError):
                    pass
            map_error(status_code=response.status_code, response=response, error_map=error_map)
            raise HttpResponseError(response=response)

        if _stream:
            deserialized = response.iter_bytes() if _decompress else response.iter_raw()
        else:
            deserialized = _deserialize(_models.RootResponse, response.json())

        if cls:
            return cls(pipeline_response, deserialized, {})  # type: ignore

        return deserialized  # type: ignore

    async def get_plan_receipts(self, plan_id: str, **kwargs: Any) -> Union[_models.PlanReceiptsResponse, Any]:
        """Get formatted receipts for a plan. Equivalent to db.ts getPlanReceipts().

        :param plan_id: Required.
        :type plan_id: str
        :return: PlanReceiptsResponse or any
        :rtype: ~org.nexus.conduitkernel.models.PlanReceiptsResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """
        error_map: MutableMapping = {
            401: ClientAuthenticationError,
            404: ResourceNotFoundError,
            409: ResourceExistsError,
            304: ResourceNotModifiedError,
        }
        error_map.update(kwargs.pop("error_map", {}) or {})

        _headers = kwargs.pop("headers", {}) or {}
        _params = kwargs.pop("params", {}) or {}

        cls: ClsType[Union[_models.PlanReceiptsResponse, Any]] = kwargs.pop("cls", None)

        _request = build_conduitkernel_get_plan_receipts_request(
            plan_id=plan_id,
            headers=_headers,
            params=_params,
        )
        path_format_arguments = {
            "endpoint": self._serialize.url("self._config.endpoint", self._config.endpoint, "str", skip_quote=True),
        }
        _request.url = self._client.format_url(_request.url, **path_format_arguments)

        _decompress = kwargs.pop("decompress", True)
        _stream = kwargs.pop("stream", False)
        pipeline_response: PipelineResponse = await self._client.pipeline.run(  # type: ignore
            _request, stream=_stream, **kwargs
        )

        response = pipeline_response.http_response

        if response.status_code not in [200]:
            if _stream:
                try:
                    await response.read()  # Load the body in memory and close the socket
                except (StreamConsumedError, StreamClosedError):
                    pass
            map_error(status_code=response.status_code, response=response, error_map=error_map)
            raise HttpResponseError(response=response)

        if _stream:
            deserialized = response.iter_bytes() if _decompress else response.iter_raw()
        else:
            deserialized = _deserialize(Union[_models.PlanReceiptsResponse, Any], response.json())

        if cls:
            return cls(pipeline_response, deserialized, {})  # type: ignore

        return deserialized  # type: ignore

    async def get_receipts_raw(self, plan_id: str, **kwargs: Any) -> Union[_models.PlanRawReceiptsResponse, Any]:
        """Get raw receipt rows for a plan. Equivalent to db.ts getReceiptsForPlan().

        :param plan_id: Required.
        :type plan_id: str
        :return: PlanRawReceiptsResponse or any
        :rtype: ~org.nexus.conduitkernel.models.PlanRawReceiptsResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """
        error_map: MutableMapping = {
            401: ClientAuthenticationError,
            404: ResourceNotFoundError,
            409: ResourceExistsError,
            304: ResourceNotModifiedError,
        }
        error_map.update(kwargs.pop("error_map", {}) or {})

        _headers = kwargs.pop("headers", {}) or {}
        _params = kwargs.pop("params", {}) or {}

        cls: ClsType[Union[_models.PlanRawReceiptsResponse, Any]] = kwargs.pop("cls", None)

        _request = build_conduitkernel_get_receipts_raw_request(
            plan_id=plan_id,
            headers=_headers,
            params=_params,
        )
        path_format_arguments = {
            "endpoint": self._serialize.url("self._config.endpoint", self._config.endpoint, "str", skip_quote=True),
        }
        _request.url = self._client.format_url(_request.url, **path_format_arguments)

        _decompress = kwargs.pop("decompress", True)
        _stream = kwargs.pop("stream", False)
        pipeline_response: PipelineResponse = await self._client.pipeline.run(  # type: ignore
            _request, stream=_stream, **kwargs
        )

        response = pipeline_response.http_response

        if response.status_code not in [200]:
            if _stream:
                try:
                    await response.read()  # Load the body in memory and close the socket
                except (StreamConsumedError, StreamClosedError):
                    pass
            map_error(status_code=response.status_code, response=response, error_map=error_map)
            raise HttpResponseError(response=response)

        if _stream:
            deserialized = response.iter_bytes() if _decompress else response.iter_raw()
        else:
            deserialized = _deserialize(Union[_models.PlanRawReceiptsResponse, Any], response.json())

        if cls:
            return cls(pipeline_response, deserialized, {})  # type: ignore

        return deserialized  # type: ignore

    async def get_latest_receipt_type(
        self, plan_id: str, **kwargs: Any
    ) -> Union[_models.LatestReceiptTypeResponse, Any]:
        """Get the latest receipt type for a plan. Equivalent to db.ts getLatestReceiptType().

        :param plan_id: Required.
        :type plan_id: str
        :return: LatestReceiptTypeResponse or any
        :rtype: ~org.nexus.conduitkernel.models.LatestReceiptTypeResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """
        error_map: MutableMapping = {
            401: ClientAuthenticationError,
            404: ResourceNotFoundError,
            409: ResourceExistsError,
            304: ResourceNotModifiedError,
        }
        error_map.update(kwargs.pop("error_map", {}) or {})

        _headers = kwargs.pop("headers", {}) or {}
        _params = kwargs.pop("params", {}) or {}

        cls: ClsType[Union[_models.LatestReceiptTypeResponse, Any]] = kwargs.pop("cls", None)

        _request = build_conduitkernel_get_latest_receipt_type_request(
            plan_id=plan_id,
            headers=_headers,
            params=_params,
        )
        path_format_arguments = {
            "endpoint": self._serialize.url("self._config.endpoint", self._config.endpoint, "str", skip_quote=True),
        }
        _request.url = self._client.format_url(_request.url, **path_format_arguments)

        _decompress = kwargs.pop("decompress", True)
        _stream = kwargs.pop("stream", False)
        pipeline_response: PipelineResponse = await self._client.pipeline.run(  # type: ignore
            _request, stream=_stream, **kwargs
        )

        response = pipeline_response.http_response

        if response.status_code not in [200]:
            if _stream:
                try:
                    await response.read()  # Load the body in memory and close the socket
                except (StreamConsumedError, StreamClosedError):
                    pass
            map_error(status_code=response.status_code, response=response, error_map=error_map)
            raise HttpResponseError(response=response)

        if _stream:
            deserialized = response.iter_bytes() if _decompress else response.iter_raw()
        else:
            deserialized = _deserialize(Union[_models.LatestReceiptTypeResponse, Any], response.json())

        if cls:
            return cls(pipeline_response, deserialized, {})  # type: ignore

        return deserialized  # type: ignore

    @overload
    async def insert_receipt(
        self, body: _models.ReceiptInsertRequest, *, content_type: str = "application/json", **kwargs: Any
    ) -> Union[_models.ReceiptInsertResponse, Any]:
        """Insert a receipt. C1 single persistence path for HTTP channel. Delegates to
        DBAdapter.insert_receipt with idempotency key (caller's id) and provenance stamping.

        :param body: Required.
        :type body: ~org.nexus.conduitkernel.models.ReceiptInsertRequest
        :keyword content_type: Body Parameter content-type. Content type parameter for JSON body.
         Default value is "application/json".
        :paramtype content_type: str
        :return: ReceiptInsertResponse or any
        :rtype: ~org.nexus.conduitkernel.models.ReceiptInsertResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """

    @overload
    async def insert_receipt(
        self, body: JSON, *, content_type: str = "application/json", **kwargs: Any
    ) -> Union[_models.ReceiptInsertResponse, Any]:
        """Insert a receipt. C1 single persistence path for HTTP channel. Delegates to
        DBAdapter.insert_receipt with idempotency key (caller's id) and provenance stamping.

        :param body: Required.
        :type body: JSON
        :keyword content_type: Body Parameter content-type. Content type parameter for JSON body.
         Default value is "application/json".
        :paramtype content_type: str
        :return: ReceiptInsertResponse or any
        :rtype: ~org.nexus.conduitkernel.models.ReceiptInsertResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """

    @overload
    async def insert_receipt(
        self, body: IO[bytes], *, content_type: str = "application/json", **kwargs: Any
    ) -> Union[_models.ReceiptInsertResponse, Any]:
        """Insert a receipt. C1 single persistence path for HTTP channel. Delegates to
        DBAdapter.insert_receipt with idempotency key (caller's id) and provenance stamping.

        :param body: Required.
        :type body: IO[bytes]
        :keyword content_type: Body Parameter content-type. Content type parameter for binary body.
         Default value is "application/json".
        :paramtype content_type: str
        :return: ReceiptInsertResponse or any
        :rtype: ~org.nexus.conduitkernel.models.ReceiptInsertResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """

    async def insert_receipt(
        self, body: Union[_models.ReceiptInsertRequest, JSON, IO[bytes]], **kwargs: Any
    ) -> Union[_models.ReceiptInsertResponse, Any]:
        """Insert a receipt. C1 single persistence path for HTTP channel. Delegates to
        DBAdapter.insert_receipt with idempotency key (caller's id) and provenance stamping.

        :param body: Is one of the following types: ReceiptInsertRequest, JSON, IO[bytes] Required.
        :type body: ~org.nexus.conduitkernel.models.ReceiptInsertRequest or JSON or IO[bytes]
        :return: ReceiptInsertResponse or any
        :rtype: ~org.nexus.conduitkernel.models.ReceiptInsertResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """
        error_map: MutableMapping = {
            401: ClientAuthenticationError,
            404: ResourceNotFoundError,
            409: ResourceExistsError,
            304: ResourceNotModifiedError,
        }
        error_map.update(kwargs.pop("error_map", {}) or {})

        _headers = case_insensitive_dict(kwargs.pop("headers", {}) or {})
        _params = kwargs.pop("params", {}) or {}

        content_type: Optional[str] = kwargs.pop("content_type", _headers.pop("Content-Type", None))
        cls: ClsType[Union[_models.ReceiptInsertResponse, Any]] = kwargs.pop("cls", None)

        content_type = content_type or "application/json"
        _content = None
        if isinstance(body, (IOBase, bytes)):
            _content = body
        else:
            _content = json.dumps(body, cls=SdkJSONEncoder, exclude_readonly=True)  # type: ignore

        _request = build_conduitkernel_insert_receipt_request(
            content_type=content_type,
            content=_content,
            headers=_headers,
            params=_params,
        )
        path_format_arguments = {
            "endpoint": self._serialize.url("self._config.endpoint", self._config.endpoint, "str", skip_quote=True),
        }
        _request.url = self._client.format_url(_request.url, **path_format_arguments)

        _decompress = kwargs.pop("decompress", True)
        _stream = kwargs.pop("stream", False)
        pipeline_response: PipelineResponse = await self._client.pipeline.run(  # type: ignore
            _request, stream=_stream, **kwargs
        )

        response = pipeline_response.http_response

        if response.status_code not in [200]:
            if _stream:
                try:
                    await response.read()  # Load the body in memory and close the socket
                except (StreamConsumedError, StreamClosedError):
                    pass
            map_error(status_code=response.status_code, response=response, error_map=error_map)
            raise HttpResponseError(response=response)

        if _stream:
            deserialized = response.iter_bytes() if _decompress else response.iter_raw()
        else:
            deserialized = _deserialize(Union[_models.ReceiptInsertResponse, Any], response.json())

        if cls:
            return cls(pipeline_response, deserialized, {})  # type: ignore

        return deserialized  # type: ignore

    async def delete_receipts(
        self, plan_id: str, *, types: str, **kwargs: Any
    ) -> Union[_models.DeleteReceiptsResponse, Any]:
        """Delete receipts by plan and type (unblock family only: BLOCK, PLAN_BLOCK, CANCELLED,
        ABANDONED). Constrained to unblock_plan workflow per architect ruling fcec95a2. Query param:
        types (comma-separated).

        :param plan_id: Required.
        :type plan_id: str
        :keyword types: Required.
        :paramtype types: str
        :return: DeleteReceiptsResponse or any
        :rtype: ~org.nexus.conduitkernel.models.DeleteReceiptsResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """
        error_map: MutableMapping = {
            401: ClientAuthenticationError,
            404: ResourceNotFoundError,
            409: ResourceExistsError,
            304: ResourceNotModifiedError,
        }
        error_map.update(kwargs.pop("error_map", {}) or {})

        _headers = kwargs.pop("headers", {}) or {}
        _params = kwargs.pop("params", {}) or {}

        cls: ClsType[Union[_models.DeleteReceiptsResponse, Any]] = kwargs.pop("cls", None)

        _request = build_conduitkernel_delete_receipts_request(
            plan_id=plan_id,
            types=types,
            headers=_headers,
            params=_params,
        )
        path_format_arguments = {
            "endpoint": self._serialize.url("self._config.endpoint", self._config.endpoint, "str", skip_quote=True),
        }
        _request.url = self._client.format_url(_request.url, **path_format_arguments)

        _decompress = kwargs.pop("decompress", True)
        _stream = kwargs.pop("stream", False)
        pipeline_response: PipelineResponse = await self._client.pipeline.run(  # type: ignore
            _request, stream=_stream, **kwargs
        )

        response = pipeline_response.http_response

        if response.status_code not in [200]:
            if _stream:
                try:
                    await response.read()  # Load the body in memory and close the socket
                except (StreamConsumedError, StreamClosedError):
                    pass
            map_error(status_code=response.status_code, response=response, error_map=error_map)
            raise HttpResponseError(response=response)

        if _stream:
            deserialized = response.iter_bytes() if _decompress else response.iter_raw()
        else:
            deserialized = _deserialize(Union[_models.DeleteReceiptsResponse, Any], response.json())

        if cls:
            return cls(pipeline_response, deserialized, {})  # type: ignore

        return deserialized  # type: ignore

    async def get_state(self, *, view: Optional[str] = None, **kwargs: Any) -> Union[_models.SystemInfoResponse, Any]:
        """Get kernel state summary or full view. Query param 'view': 'summary' (default) or 'full'.

        :keyword view: Default value is None.
        :paramtype view: str
        :return: SystemInfoResponse or any
        :rtype: ~org.nexus.conduitkernel.models.SystemInfoResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """
        error_map: MutableMapping = {
            401: ClientAuthenticationError,
            404: ResourceNotFoundError,
            409: ResourceExistsError,
            304: ResourceNotModifiedError,
        }
        error_map.update(kwargs.pop("error_map", {}) or {})

        _headers = kwargs.pop("headers", {}) or {}
        _params = kwargs.pop("params", {}) or {}

        cls: ClsType[Union[_models.SystemInfoResponse, Any]] = kwargs.pop("cls", None)

        _request = build_conduitkernel_get_state_request(
            view=view,
            headers=_headers,
            params=_params,
        )
        path_format_arguments = {
            "endpoint": self._serialize.url("self._config.endpoint", self._config.endpoint, "str", skip_quote=True),
        }
        _request.url = self._client.format_url(_request.url, **path_format_arguments)

        _decompress = kwargs.pop("decompress", True)
        _stream = kwargs.pop("stream", False)
        pipeline_response: PipelineResponse = await self._client.pipeline.run(  # type: ignore
            _request, stream=_stream, **kwargs
        )

        response = pipeline_response.http_response

        if response.status_code not in [200]:
            if _stream:
                try:
                    await response.read()  # Load the body in memory and close the socket
                except (StreamConsumedError, StreamClosedError):
                    pass
            map_error(status_code=response.status_code, response=response, error_map=error_map)
            raise HttpResponseError(response=response)

        if _stream:
            deserialized = response.iter_bytes() if _decompress else response.iter_raw()
        else:
            deserialized = _deserialize(Union[_models.SystemInfoResponse, Any], response.json())

        if cls:
            return cls(pipeline_response, deserialized, {})  # type: ignore

        return deserialized  # type: ignore

    async def get_identity(self, identity_id: str, **kwargs: Any) -> Union[_models.IdentityResolutionResponse, Any]:
        """Resolve an identity by ID, node_id, or bare plan number. Returns identity details plus
        connected graph edges.

        :param identity_id: Required.
        :type identity_id: str
        :return: IdentityResolutionResponse or any
        :rtype: ~org.nexus.conduitkernel.models.IdentityResolutionResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """
        error_map: MutableMapping = {
            401: ClientAuthenticationError,
            404: ResourceNotFoundError,
            409: ResourceExistsError,
            304: ResourceNotModifiedError,
        }
        error_map.update(kwargs.pop("error_map", {}) or {})

        _headers = kwargs.pop("headers", {}) or {}
        _params = kwargs.pop("params", {}) or {}

        cls: ClsType[Union[_models.IdentityResolutionResponse, Any]] = kwargs.pop("cls", None)

        _request = build_conduitkernel_get_identity_request(
            identity_id=identity_id,
            headers=_headers,
            params=_params,
        )
        path_format_arguments = {
            "endpoint": self._serialize.url("self._config.endpoint", self._config.endpoint, "str", skip_quote=True),
        }
        _request.url = self._client.format_url(_request.url, **path_format_arguments)

        _decompress = kwargs.pop("decompress", True)
        _stream = kwargs.pop("stream", False)
        pipeline_response: PipelineResponse = await self._client.pipeline.run(  # type: ignore
            _request, stream=_stream, **kwargs
        )

        response = pipeline_response.http_response

        if response.status_code not in [200]:
            if _stream:
                try:
                    await response.read()  # Load the body in memory and close the socket
                except (StreamConsumedError, StreamClosedError):
                    pass
            map_error(status_code=response.status_code, response=response, error_map=error_map)
            raise HttpResponseError(response=response)

        if _stream:
            deserialized = response.iter_bytes() if _decompress else response.iter_raw()
        else:
            deserialized = _deserialize(Union[_models.IdentityResolutionResponse, Any], response.json())

        if cls:
            return cls(pipeline_response, deserialized, {})  # type: ignore

        return deserialized  # type: ignore

    async def get_receipt(self, receipt_id: str, **kwargs: Any) -> Union[_models.ReceiptByIdResponse, Any]:
        """Look up a single receipt by its receipt UUID.

        :param receipt_id: Required.
        :type receipt_id: str
        :return: ReceiptByIdResponse or any
        :rtype: ~org.nexus.conduitkernel.models.ReceiptByIdResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """
        error_map: MutableMapping = {
            401: ClientAuthenticationError,
            404: ResourceNotFoundError,
            409: ResourceExistsError,
            304: ResourceNotModifiedError,
        }
        error_map.update(kwargs.pop("error_map", {}) or {})

        _headers = kwargs.pop("headers", {}) or {}
        _params = kwargs.pop("params", {}) or {}

        cls: ClsType[Union[_models.ReceiptByIdResponse, Any]] = kwargs.pop("cls", None)

        _request = build_conduitkernel_get_receipt_request(
            receipt_id=receipt_id,
            headers=_headers,
            params=_params,
        )
        path_format_arguments = {
            "endpoint": self._serialize.url("self._config.endpoint", self._config.endpoint, "str", skip_quote=True),
        }
        _request.url = self._client.format_url(_request.url, **path_format_arguments)

        _decompress = kwargs.pop("decompress", True)
        _stream = kwargs.pop("stream", False)
        pipeline_response: PipelineResponse = await self._client.pipeline.run(  # type: ignore
            _request, stream=_stream, **kwargs
        )

        response = pipeline_response.http_response

        if response.status_code not in [200]:
            if _stream:
                try:
                    await response.read()  # Load the body in memory and close the socket
                except (StreamConsumedError, StreamClosedError):
                    pass
            map_error(status_code=response.status_code, response=response, error_map=error_map)
            raise HttpResponseError(response=response)

        if _stream:
            deserialized = response.iter_bytes() if _decompress else response.iter_raw()
        else:
            deserialized = _deserialize(Union[_models.ReceiptByIdResponse, Any], response.json())

        if cls:
            return cls(pipeline_response, deserialized, {})  # type: ignore

        return deserialized  # type: ignore

    async def get_receipts_by_plan(self, plan_num: str, **kwargs: Any) -> Union[_models.ReceiptsByPlanResponse, Any]:
        """List receipts whose plan_id matches the given plan number.

        :param plan_num: Required.
        :type plan_num: str
        :return: ReceiptsByPlanResponse or any
        :rtype: ~org.nexus.conduitkernel.models.ReceiptsByPlanResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """
        error_map: MutableMapping = {
            401: ClientAuthenticationError,
            404: ResourceNotFoundError,
            409: ResourceExistsError,
            304: ResourceNotModifiedError,
        }
        error_map.update(kwargs.pop("error_map", {}) or {})

        _headers = kwargs.pop("headers", {}) or {}
        _params = kwargs.pop("params", {}) or {}

        cls: ClsType[Union[_models.ReceiptsByPlanResponse, Any]] = kwargs.pop("cls", None)

        _request = build_conduitkernel_get_receipts_by_plan_request(
            plan_num=plan_num,
            headers=_headers,
            params=_params,
        )
        path_format_arguments = {
            "endpoint": self._serialize.url("self._config.endpoint", self._config.endpoint, "str", skip_quote=True),
        }
        _request.url = self._client.format_url(_request.url, **path_format_arguments)

        _decompress = kwargs.pop("decompress", True)
        _stream = kwargs.pop("stream", False)
        pipeline_response: PipelineResponse = await self._client.pipeline.run(  # type: ignore
            _request, stream=_stream, **kwargs
        )

        response = pipeline_response.http_response

        if response.status_code not in [200]:
            if _stream:
                try:
                    await response.read()  # Load the body in memory and close the socket
                except (StreamConsumedError, StreamClosedError):
                    pass
            map_error(status_code=response.status_code, response=response, error_map=error_map)
            raise HttpResponseError(response=response)

        if _stream:
            deserialized = response.iter_bytes() if _decompress else response.iter_raw()
        else:
            deserialized = _deserialize(Union[_models.ReceiptsByPlanResponse, Any], response.json())

        if cls:
            return cls(pipeline_response, deserialized, {})  # type: ignore

        return deserialized  # type: ignore

    async def get_graph(
        self, *, cursor: Optional[str] = None, limit: Optional[int] = None, **kwargs: Any
    ) -> Union[_models.GraphResponse, Any]:
        """Get the cross-plan graph with cursor-based pagination. Query params: cursor (opaque), limit
        (1-5000, default 200).

        :keyword cursor: Default value is None.
        :paramtype cursor: str
        :keyword limit: Default value is None.
        :paramtype limit: int
        :return: GraphResponse or any
        :rtype: ~org.nexus.conduitkernel.models.GraphResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """
        error_map: MutableMapping = {
            401: ClientAuthenticationError,
            404: ResourceNotFoundError,
            409: ResourceExistsError,
            304: ResourceNotModifiedError,
        }
        error_map.update(kwargs.pop("error_map", {}) or {})

        _headers = kwargs.pop("headers", {}) or {}
        _params = kwargs.pop("params", {}) or {}

        cls: ClsType[Union[_models.GraphResponse, Any]] = kwargs.pop("cls", None)

        _request = build_conduitkernel_get_graph_request(
            cursor=cursor,
            limit=limit,
            headers=_headers,
            params=_params,
        )
        path_format_arguments = {
            "endpoint": self._serialize.url("self._config.endpoint", self._config.endpoint, "str", skip_quote=True),
        }
        _request.url = self._client.format_url(_request.url, **path_format_arguments)

        _decompress = kwargs.pop("decompress", True)
        _stream = kwargs.pop("stream", False)
        pipeline_response: PipelineResponse = await self._client.pipeline.run(  # type: ignore
            _request, stream=_stream, **kwargs
        )

        response = pipeline_response.http_response

        if response.status_code not in [200]:
            if _stream:
                try:
                    await response.read()  # Load the body in memory and close the socket
                except (StreamConsumedError, StreamClosedError):
                    pass
            map_error(status_code=response.status_code, response=response, error_map=error_map)
            raise HttpResponseError(response=response)

        if _stream:
            deserialized = response.iter_bytes() if _decompress else response.iter_raw()
        else:
            deserialized = _deserialize(Union[_models.GraphResponse, Any], response.json())

        if cls:
            return cls(pipeline_response, deserialized, {})  # type: ignore

        return deserialized  # type: ignore

    async def get_plan_detail(self, plan_num: str, **kwargs: Any) -> Union[_models.PlanDetailResponse, Any]:
        """Get detailed profile of a plan: identity, receipt timeline, WRP state machine position, valid
        transitions, graph edges.

        :param plan_num: Required.
        :type plan_num: str
        :return: PlanDetailResponse or any
        :rtype: ~org.nexus.conduitkernel.models.PlanDetailResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """
        error_map: MutableMapping = {
            401: ClientAuthenticationError,
            404: ResourceNotFoundError,
            409: ResourceExistsError,
            304: ResourceNotModifiedError,
        }
        error_map.update(kwargs.pop("error_map", {}) or {})

        _headers = kwargs.pop("headers", {}) or {}
        _params = kwargs.pop("params", {}) or {}

        cls: ClsType[Union[_models.PlanDetailResponse, Any]] = kwargs.pop("cls", None)

        _request = build_conduitkernel_get_plan_detail_request(
            plan_num=plan_num,
            headers=_headers,
            params=_params,
        )
        path_format_arguments = {
            "endpoint": self._serialize.url("self._config.endpoint", self._config.endpoint, "str", skip_quote=True),
        }
        _request.url = self._client.format_url(_request.url, **path_format_arguments)

        _decompress = kwargs.pop("decompress", True)
        _stream = kwargs.pop("stream", False)
        pipeline_response: PipelineResponse = await self._client.pipeline.run(  # type: ignore
            _request, stream=_stream, **kwargs
        )

        response = pipeline_response.http_response

        if response.status_code not in [200]:
            if _stream:
                try:
                    await response.read()  # Load the body in memory and close the socket
                except (StreamConsumedError, StreamClosedError):
                    pass
            map_error(status_code=response.status_code, response=response, error_map=error_map)
            raise HttpResponseError(response=response)

        if _stream:
            deserialized = response.iter_bytes() if _decompress else response.iter_raw()
        else:
            deserialized = _deserialize(Union[_models.PlanDetailResponse, Any], response.json())

        if cls:
            return cls(pipeline_response, deserialized, {})  # type: ignore

        return deserialized  # type: ignore

    async def state_health(self, **kwargs: Any) -> Union[_models.StateHealthResponse, Any]:
        """State subsystem health check.

        :return: StateHealthResponse or any
        :rtype: ~org.nexus.conduitkernel.models.StateHealthResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """
        error_map: MutableMapping = {
            401: ClientAuthenticationError,
            404: ResourceNotFoundError,
            409: ResourceExistsError,
            304: ResourceNotModifiedError,
        }
        error_map.update(kwargs.pop("error_map", {}) or {})

        _headers = kwargs.pop("headers", {}) or {}
        _params = kwargs.pop("params", {}) or {}

        cls: ClsType[Union[_models.StateHealthResponse, Any]] = kwargs.pop("cls", None)

        _request = build_conduitkernel_state_health_request(
            headers=_headers,
            params=_params,
        )
        path_format_arguments = {
            "endpoint": self._serialize.url("self._config.endpoint", self._config.endpoint, "str", skip_quote=True),
        }
        _request.url = self._client.format_url(_request.url, **path_format_arguments)

        _decompress = kwargs.pop("decompress", True)
        _stream = kwargs.pop("stream", False)
        pipeline_response: PipelineResponse = await self._client.pipeline.run(  # type: ignore
            _request, stream=_stream, **kwargs
        )

        response = pipeline_response.http_response

        if response.status_code not in [200]:
            if _stream:
                try:
                    await response.read()  # Load the body in memory and close the socket
                except (StreamConsumedError, StreamClosedError):
                    pass
            map_error(status_code=response.status_code, response=response, error_map=error_map)
            raise HttpResponseError(response=response)

        if _stream:
            deserialized = response.iter_bytes() if _decompress else response.iter_raw()
        else:
            deserialized = _deserialize(Union[_models.StateHealthResponse, Any], response.json())

        if cls:
            return cls(pipeline_response, deserialized, {})  # type: ignore

        return deserialized  # type: ignore

    async def get_lineage(
        self, *, version: Optional[int] = None, limit: Optional[int] = None, **kwargs: Any
    ) -> Union[_models.LineageResponse, Any]:
        """Get lineage events from database. Query params: version (optional filter), limit (default 100).

        :keyword version: Default value is None.
        :paramtype version: int
        :keyword limit: Default value is None.
        :paramtype limit: int
        :return: LineageResponse or any
        :rtype: ~org.nexus.conduitkernel.models.LineageResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """
        error_map: MutableMapping = {
            401: ClientAuthenticationError,
            404: ResourceNotFoundError,
            409: ResourceExistsError,
            304: ResourceNotModifiedError,
        }
        error_map.update(kwargs.pop("error_map", {}) or {})

        _headers = kwargs.pop("headers", {}) or {}
        _params = kwargs.pop("params", {}) or {}

        cls: ClsType[Union[_models.LineageResponse, Any]] = kwargs.pop("cls", None)

        _request = build_conduitkernel_get_lineage_request(
            version=version,
            limit=limit,
            headers=_headers,
            params=_params,
        )
        path_format_arguments = {
            "endpoint": self._serialize.url("self._config.endpoint", self._config.endpoint, "str", skip_quote=True),
        }
        _request.url = self._client.format_url(_request.url, **path_format_arguments)

        _decompress = kwargs.pop("decompress", True)
        _stream = kwargs.pop("stream", False)
        pipeline_response: PipelineResponse = await self._client.pipeline.run(  # type: ignore
            _request, stream=_stream, **kwargs
        )

        response = pipeline_response.http_response

        if response.status_code not in [200]:
            if _stream:
                try:
                    await response.read()  # Load the body in memory and close the socket
                except (StreamConsumedError, StreamClosedError):
                    pass
            map_error(status_code=response.status_code, response=response, error_map=error_map)
            raise HttpResponseError(response=response)

        if _stream:
            deserialized = response.iter_bytes() if _decompress else response.iter_raw()
        else:
            deserialized = _deserialize(Union[_models.LineageResponse, Any], response.json())

        if cls:
            return cls(pipeline_response, deserialized, {})  # type: ignore

        return deserialized  # type: ignore

    async def list_replay(self, **kwargs: Any) -> Union[_models.ReplayResponse, Any]:
        """List replay runs.

        :return: ReplayResponse or any
        :rtype: ~org.nexus.conduitkernel.models.ReplayResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """
        error_map: MutableMapping = {
            401: ClientAuthenticationError,
            404: ResourceNotFoundError,
            409: ResourceExistsError,
            304: ResourceNotModifiedError,
        }
        error_map.update(kwargs.pop("error_map", {}) or {})

        _headers = kwargs.pop("headers", {}) or {}
        _params = kwargs.pop("params", {}) or {}

        cls: ClsType[Union[_models.ReplayResponse, Any]] = kwargs.pop("cls", None)

        _request = build_conduitkernel_list_replay_request(
            headers=_headers,
            params=_params,
        )
        path_format_arguments = {
            "endpoint": self._serialize.url("self._config.endpoint", self._config.endpoint, "str", skip_quote=True),
        }
        _request.url = self._client.format_url(_request.url, **path_format_arguments)

        _decompress = kwargs.pop("decompress", True)
        _stream = kwargs.pop("stream", False)
        pipeline_response: PipelineResponse = await self._client.pipeline.run(  # type: ignore
            _request, stream=_stream, **kwargs
        )

        response = pipeline_response.http_response

        if response.status_code not in [200]:
            if _stream:
                try:
                    await response.read()  # Load the body in memory and close the socket
                except (StreamConsumedError, StreamClosedError):
                    pass
            map_error(status_code=response.status_code, response=response, error_map=error_map)
            raise HttpResponseError(response=response)

        if _stream:
            deserialized = response.iter_bytes() if _decompress else response.iter_raw()
        else:
            deserialized = _deserialize(Union[_models.ReplayResponse, Any], response.json())

        if cls:
            return cls(pipeline_response, deserialized, {})  # type: ignore

        return deserialized  # type: ignore

    async def compare_replay(self, **kwargs: Any) -> Union[_models.CompareResponse, Any]:
        """Compare replay runs.

        :return: CompareResponse or any
        :rtype: ~org.nexus.conduitkernel.models.CompareResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """
        error_map: MutableMapping = {
            401: ClientAuthenticationError,
            404: ResourceNotFoundError,
            409: ResourceExistsError,
            304: ResourceNotModifiedError,
        }
        error_map.update(kwargs.pop("error_map", {}) or {})

        _headers = kwargs.pop("headers", {}) or {}
        _params = kwargs.pop("params", {}) or {}

        cls: ClsType[Union[_models.CompareResponse, Any]] = kwargs.pop("cls", None)

        _request = build_conduitkernel_compare_replay_request(
            headers=_headers,
            params=_params,
        )
        path_format_arguments = {
            "endpoint": self._serialize.url("self._config.endpoint", self._config.endpoint, "str", skip_quote=True),
        }
        _request.url = self._client.format_url(_request.url, **path_format_arguments)

        _decompress = kwargs.pop("decompress", True)
        _stream = kwargs.pop("stream", False)
        pipeline_response: PipelineResponse = await self._client.pipeline.run(  # type: ignore
            _request, stream=_stream, **kwargs
        )

        response = pipeline_response.http_response

        if response.status_code not in [200]:
            if _stream:
                try:
                    await response.read()  # Load the body in memory and close the socket
                except (StreamConsumedError, StreamClosedError):
                    pass
            map_error(status_code=response.status_code, response=response, error_map=error_map)
            raise HttpResponseError(response=response)

        if _stream:
            deserialized = response.iter_bytes() if _decompress else response.iter_raw()
        else:
            deserialized = _deserialize(Union[_models.CompareResponse, Any], response.json())

        if cls:
            return cls(pipeline_response, deserialized, {})  # type: ignore

        return deserialized  # type: ignore

    async def list_sessions(
        self, *, running_only: Optional[bool] = None, **kwargs: Any
    ) -> Union[_models.SessionListResponse, Any]:
        """List all sessions. Query param: running_only (boolean).

        :keyword running_only: Default value is None.
        :paramtype running_only: bool
        :return: SessionListResponse or any
        :rtype: ~org.nexus.conduitkernel.models.SessionListResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """
        error_map: MutableMapping = {
            401: ClientAuthenticationError,
            404: ResourceNotFoundError,
            409: ResourceExistsError,
            304: ResourceNotModifiedError,
        }
        error_map.update(kwargs.pop("error_map", {}) or {})

        _headers = kwargs.pop("headers", {}) or {}
        _params = kwargs.pop("params", {}) or {}

        cls: ClsType[Union[_models.SessionListResponse, Any]] = kwargs.pop("cls", None)

        _request = build_conduitkernel_list_sessions_request(
            running_only=running_only,
            headers=_headers,
            params=_params,
        )
        path_format_arguments = {
            "endpoint": self._serialize.url("self._config.endpoint", self._config.endpoint, "str", skip_quote=True),
        }
        _request.url = self._client.format_url(_request.url, **path_format_arguments)

        _decompress = kwargs.pop("decompress", True)
        _stream = kwargs.pop("stream", False)
        pipeline_response: PipelineResponse = await self._client.pipeline.run(  # type: ignore
            _request, stream=_stream, **kwargs
        )

        response = pipeline_response.http_response

        if response.status_code not in [200]:
            if _stream:
                try:
                    await response.read()  # Load the body in memory and close the socket
                except (StreamConsumedError, StreamClosedError):
                    pass
            map_error(status_code=response.status_code, response=response, error_map=error_map)
            raise HttpResponseError(response=response)

        if _stream:
            deserialized = response.iter_bytes() if _decompress else response.iter_raw()
        else:
            deserialized = _deserialize(Union[_models.SessionListResponse, Any], response.json())

        if cls:
            return cls(pipeline_response, deserialized, {})  # type: ignore

        return deserialized  # type: ignore

    async def get_running_sessions(self, **kwargs: Any) -> Union[_models.SessionListResponse, Any]:
        """Get currently running sessions.

        :return: SessionListResponse or any
        :rtype: ~org.nexus.conduitkernel.models.SessionListResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """
        error_map: MutableMapping = {
            401: ClientAuthenticationError,
            404: ResourceNotFoundError,
            409: ResourceExistsError,
            304: ResourceNotModifiedError,
        }
        error_map.update(kwargs.pop("error_map", {}) or {})

        _headers = kwargs.pop("headers", {}) or {}
        _params = kwargs.pop("params", {}) or {}

        cls: ClsType[Union[_models.SessionListResponse, Any]] = kwargs.pop("cls", None)

        _request = build_conduitkernel_get_running_sessions_request(
            headers=_headers,
            params=_params,
        )
        path_format_arguments = {
            "endpoint": self._serialize.url("self._config.endpoint", self._config.endpoint, "str", skip_quote=True),
        }
        _request.url = self._client.format_url(_request.url, **path_format_arguments)

        _decompress = kwargs.pop("decompress", True)
        _stream = kwargs.pop("stream", False)
        pipeline_response: PipelineResponse = await self._client.pipeline.run(  # type: ignore
            _request, stream=_stream, **kwargs
        )

        response = pipeline_response.http_response

        if response.status_code not in [200]:
            if _stream:
                try:
                    await response.read()  # Load the body in memory and close the socket
                except (StreamConsumedError, StreamClosedError):
                    pass
            map_error(status_code=response.status_code, response=response, error_map=error_map)
            raise HttpResponseError(response=response)

        if _stream:
            deserialized = response.iter_bytes() if _decompress else response.iter_raw()
        else:
            deserialized = _deserialize(Union[_models.SessionListResponse, Any], response.json())

        if cls:
            return cls(pipeline_response, deserialized, {})  # type: ignore

        return deserialized  # type: ignore

    async def get_stale_sessions(
        self, *, threshold_seconds: Optional[int] = None, **kwargs: Any
    ) -> Union[_models.SessionListResponse, Any]:
        """Detect stale sessions (no heartbeat within threshold). Query param: threshold_seconds (default
        3600).

        :keyword threshold_seconds: Default value is None.
        :paramtype threshold_seconds: int
        :return: SessionListResponse or any
        :rtype: ~org.nexus.conduitkernel.models.SessionListResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """
        error_map: MutableMapping = {
            401: ClientAuthenticationError,
            404: ResourceNotFoundError,
            409: ResourceExistsError,
            304: ResourceNotModifiedError,
        }
        error_map.update(kwargs.pop("error_map", {}) or {})

        _headers = kwargs.pop("headers", {}) or {}
        _params = kwargs.pop("params", {}) or {}

        cls: ClsType[Union[_models.SessionListResponse, Any]] = kwargs.pop("cls", None)

        _request = build_conduitkernel_get_stale_sessions_request(
            threshold_seconds=threshold_seconds,
            headers=_headers,
            params=_params,
        )
        path_format_arguments = {
            "endpoint": self._serialize.url("self._config.endpoint", self._config.endpoint, "str", skip_quote=True),
        }
        _request.url = self._client.format_url(_request.url, **path_format_arguments)

        _decompress = kwargs.pop("decompress", True)
        _stream = kwargs.pop("stream", False)
        pipeline_response: PipelineResponse = await self._client.pipeline.run(  # type: ignore
            _request, stream=_stream, **kwargs
        )

        response = pipeline_response.http_response

        if response.status_code not in [200]:
            if _stream:
                try:
                    await response.read()  # Load the body in memory and close the socket
                except (StreamConsumedError, StreamClosedError):
                    pass
            map_error(status_code=response.status_code, response=response, error_map=error_map)
            raise HttpResponseError(response=response)

        if _stream:
            deserialized = response.iter_bytes() if _decompress else response.iter_raw()
        else:
            deserialized = _deserialize(Union[_models.SessionListResponse, Any], response.json())

        if cls:
            return cls(pipeline_response, deserialized, {})  # type: ignore

        return deserialized  # type: ignore

    async def get_session(self, session_id: str, **kwargs: Any) -> Union[_models.SessionResponse, Any]:
        """Get a single session by ID.

        :param session_id: Required.
        :type session_id: str
        :return: SessionResponse or any
        :rtype: ~org.nexus.conduitkernel.models.SessionResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """
        error_map: MutableMapping = {
            401: ClientAuthenticationError,
            404: ResourceNotFoundError,
            409: ResourceExistsError,
            304: ResourceNotModifiedError,
        }
        error_map.update(kwargs.pop("error_map", {}) or {})

        _headers = kwargs.pop("headers", {}) or {}
        _params = kwargs.pop("params", {}) or {}

        cls: ClsType[Union[_models.SessionResponse, Any]] = kwargs.pop("cls", None)

        _request = build_conduitkernel_get_session_request(
            session_id=session_id,
            headers=_headers,
            params=_params,
        )
        path_format_arguments = {
            "endpoint": self._serialize.url("self._config.endpoint", self._config.endpoint, "str", skip_quote=True),
        }
        _request.url = self._client.format_url(_request.url, **path_format_arguments)

        _decompress = kwargs.pop("decompress", True)
        _stream = kwargs.pop("stream", False)
        pipeline_response: PipelineResponse = await self._client.pipeline.run(  # type: ignore
            _request, stream=_stream, **kwargs
        )

        response = pipeline_response.http_response

        if response.status_code not in [200]:
            if _stream:
                try:
                    await response.read()  # Load the body in memory and close the socket
                except (StreamConsumedError, StreamClosedError):
                    pass
            map_error(status_code=response.status_code, response=response, error_map=error_map)
            raise HttpResponseError(response=response)

        if _stream:
            deserialized = response.iter_bytes() if _decompress else response.iter_raw()
        else:
            deserialized = _deserialize(Union[_models.SessionResponse, Any], response.json())

        if cls:
            return cls(pipeline_response, deserialized, {})  # type: ignore

        return deserialized  # type: ignore

    @overload
    async def update_session_cost(
        self,
        session_id: str,
        body: _models.SessionCostUpdateRequest,
        *,
        content_type: str = "application/json",
        **kwargs: Any
    ) -> Union[_models.SessionCostUpdateResponse, Any]:
        """Update session token cost.

        :param session_id: Required.
        :type session_id: str
        :param body: Required.
        :type body: ~org.nexus.conduitkernel.models.SessionCostUpdateRequest
        :keyword content_type: Body Parameter content-type. Content type parameter for JSON body.
         Default value is "application/json".
        :paramtype content_type: str
        :return: SessionCostUpdateResponse or any
        :rtype: ~org.nexus.conduitkernel.models.SessionCostUpdateResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """

    @overload
    async def update_session_cost(
        self, session_id: str, body: JSON, *, content_type: str = "application/json", **kwargs: Any
    ) -> Union[_models.SessionCostUpdateResponse, Any]:
        """Update session token cost.

        :param session_id: Required.
        :type session_id: str
        :param body: Required.
        :type body: JSON
        :keyword content_type: Body Parameter content-type. Content type parameter for JSON body.
         Default value is "application/json".
        :paramtype content_type: str
        :return: SessionCostUpdateResponse or any
        :rtype: ~org.nexus.conduitkernel.models.SessionCostUpdateResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """

    @overload
    async def update_session_cost(
        self, session_id: str, body: IO[bytes], *, content_type: str = "application/json", **kwargs: Any
    ) -> Union[_models.SessionCostUpdateResponse, Any]:
        """Update session token cost.

        :param session_id: Required.
        :type session_id: str
        :param body: Required.
        :type body: IO[bytes]
        :keyword content_type: Body Parameter content-type. Content type parameter for binary body.
         Default value is "application/json".
        :paramtype content_type: str
        :return: SessionCostUpdateResponse or any
        :rtype: ~org.nexus.conduitkernel.models.SessionCostUpdateResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """

    async def update_session_cost(
        self, session_id: str, body: Union[_models.SessionCostUpdateRequest, JSON, IO[bytes]], **kwargs: Any
    ) -> Union[_models.SessionCostUpdateResponse, Any]:
        """Update session token cost.

        :param session_id: Required.
        :type session_id: str
        :param body: Is one of the following types: SessionCostUpdateRequest, JSON, IO[bytes] Required.
        :type body: ~org.nexus.conduitkernel.models.SessionCostUpdateRequest or JSON or IO[bytes]
        :return: SessionCostUpdateResponse or any
        :rtype: ~org.nexus.conduitkernel.models.SessionCostUpdateResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """
        error_map: MutableMapping = {
            401: ClientAuthenticationError,
            404: ResourceNotFoundError,
            409: ResourceExistsError,
            304: ResourceNotModifiedError,
        }
        error_map.update(kwargs.pop("error_map", {}) or {})

        _headers = case_insensitive_dict(kwargs.pop("headers", {}) or {})
        _params = kwargs.pop("params", {}) or {}

        content_type: Optional[str] = kwargs.pop("content_type", _headers.pop("Content-Type", None))
        cls: ClsType[Union[_models.SessionCostUpdateResponse, Any]] = kwargs.pop("cls", None)

        content_type = content_type or "application/json"
        _content = None
        if isinstance(body, (IOBase, bytes)):
            _content = body
        else:
            _content = json.dumps(body, cls=SdkJSONEncoder, exclude_readonly=True)  # type: ignore

        _request = build_conduitkernel_update_session_cost_request(
            session_id=session_id,
            content_type=content_type,
            content=_content,
            headers=_headers,
            params=_params,
        )
        path_format_arguments = {
            "endpoint": self._serialize.url("self._config.endpoint", self._config.endpoint, "str", skip_quote=True),
        }
        _request.url = self._client.format_url(_request.url, **path_format_arguments)

        _decompress = kwargs.pop("decompress", True)
        _stream = kwargs.pop("stream", False)
        pipeline_response: PipelineResponse = await self._client.pipeline.run(  # type: ignore
            _request, stream=_stream, **kwargs
        )

        response = pipeline_response.http_response

        if response.status_code not in [200]:
            if _stream:
                try:
                    await response.read()  # Load the body in memory and close the socket
                except (StreamConsumedError, StreamClosedError):
                    pass
            map_error(status_code=response.status_code, response=response, error_map=error_map)
            raise HttpResponseError(response=response)

        if _stream:
            deserialized = response.iter_bytes() if _decompress else response.iter_raw()
        else:
            deserialized = _deserialize(Union[_models.SessionCostUpdateResponse, Any], response.json())

        if cls:
            return cls(pipeline_response, deserialized, {})  # type: ignore

        return deserialized  # type: ignore

    @overload
    async def update_session_heartbeat(
        self,
        session_id: str,
        body: Optional[_models.SessionHeartbeatRequest] = None,
        *,
        content_type: str = "application/json",
        **kwargs: Any
    ) -> Union[_models.SessionHeartbeatResponse, Any]:
        """Update session heartbeat timestamp and activity.

        :param session_id: Required.
        :type session_id: str
        :param body: Default value is None.
        :type body: ~org.nexus.conduitkernel.models.SessionHeartbeatRequest
        :keyword content_type: Body Parameter content-type. Content type parameter for JSON body.
         Default value is "application/json".
        :paramtype content_type: str
        :return: SessionHeartbeatResponse or any
        :rtype: ~org.nexus.conduitkernel.models.SessionHeartbeatResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """

    @overload
    async def update_session_heartbeat(
        self, session_id: str, body: Optional[JSON] = None, *, content_type: str = "application/json", **kwargs: Any
    ) -> Union[_models.SessionHeartbeatResponse, Any]:
        """Update session heartbeat timestamp and activity.

        :param session_id: Required.
        :type session_id: str
        :param body: Default value is None.
        :type body: JSON
        :keyword content_type: Body Parameter content-type. Content type parameter for JSON body.
         Default value is "application/json".
        :paramtype content_type: str
        :return: SessionHeartbeatResponse or any
        :rtype: ~org.nexus.conduitkernel.models.SessionHeartbeatResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """

    @overload
    async def update_session_heartbeat(
        self,
        session_id: str,
        body: Optional[IO[bytes]] = None,
        *,
        content_type: str = "application/json",
        **kwargs: Any
    ) -> Union[_models.SessionHeartbeatResponse, Any]:
        """Update session heartbeat timestamp and activity.

        :param session_id: Required.
        :type session_id: str
        :param body: Default value is None.
        :type body: IO[bytes]
        :keyword content_type: Body Parameter content-type. Content type parameter for binary body.
         Default value is "application/json".
        :paramtype content_type: str
        :return: SessionHeartbeatResponse or any
        :rtype: ~org.nexus.conduitkernel.models.SessionHeartbeatResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """

    async def update_session_heartbeat(
        self,
        session_id: str,
        body: Optional[Union[_models.SessionHeartbeatRequest, JSON, IO[bytes]]] = None,
        **kwargs: Any
    ) -> Union[_models.SessionHeartbeatResponse, Any]:
        """Update session heartbeat timestamp and activity.

        :param session_id: Required.
        :type session_id: str
        :param body: Is one of the following types: SessionHeartbeatRequest, JSON, IO[bytes] Default
         value is None.
        :type body: ~org.nexus.conduitkernel.models.SessionHeartbeatRequest or JSON or IO[bytes]
        :return: SessionHeartbeatResponse or any
        :rtype: ~org.nexus.conduitkernel.models.SessionHeartbeatResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """
        error_map: MutableMapping = {
            401: ClientAuthenticationError,
            404: ResourceNotFoundError,
            409: ResourceExistsError,
            304: ResourceNotModifiedError,
        }
        error_map.update(kwargs.pop("error_map", {}) or {})

        _headers = case_insensitive_dict(kwargs.pop("headers", {}) or {})
        _params = kwargs.pop("params", {}) or {}

        content_type: Optional[str] = kwargs.pop("content_type", _headers.pop("Content-Type", None))
        content_type = content_type if body else None
        cls: ClsType[Union[_models.SessionHeartbeatResponse, Any]] = kwargs.pop("cls", None)

        content_type = content_type or "application/json" if body else None
        _content = None
        if isinstance(body, (IOBase, bytes)):
            _content = body
        else:
            if body is not None:
                _content = json.dumps(body, cls=SdkJSONEncoder, exclude_readonly=True)  # type: ignore
            else:
                _content = None

        _request = build_conduitkernel_update_session_heartbeat_request(
            session_id=session_id,
            content_type=content_type,
            content=_content,
            headers=_headers,
            params=_params,
        )
        path_format_arguments = {
            "endpoint": self._serialize.url("self._config.endpoint", self._config.endpoint, "str", skip_quote=True),
        }
        _request.url = self._client.format_url(_request.url, **path_format_arguments)

        _decompress = kwargs.pop("decompress", True)
        _stream = kwargs.pop("stream", False)
        pipeline_response: PipelineResponse = await self._client.pipeline.run(  # type: ignore
            _request, stream=_stream, **kwargs
        )

        response = pipeline_response.http_response

        if response.status_code not in [200]:
            if _stream:
                try:
                    await response.read()  # Load the body in memory and close the socket
                except (StreamConsumedError, StreamClosedError):
                    pass
            map_error(status_code=response.status_code, response=response, error_map=error_map)
            raise HttpResponseError(response=response)

        if _stream:
            deserialized = response.iter_bytes() if _decompress else response.iter_raw()
        else:
            deserialized = _deserialize(Union[_models.SessionHeartbeatResponse, Any], response.json())

        if cls:
            return cls(pipeline_response, deserialized, {})  # type: ignore

        return deserialized  # type: ignore

    async def kill_session(self, session_id: str, **kwargs: Any) -> Union[_models.SessionKillResult, Any]:
        """Kill a running session (SIGKILL process, close session, release tickets).

        :param session_id: Required.
        :type session_id: str
        :return: SessionKillResult or any
        :rtype: ~org.nexus.conduitkernel.models.SessionKillResult or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """
        error_map: MutableMapping = {
            401: ClientAuthenticationError,
            404: ResourceNotFoundError,
            409: ResourceExistsError,
            304: ResourceNotModifiedError,
        }
        error_map.update(kwargs.pop("error_map", {}) or {})

        _headers = kwargs.pop("headers", {}) or {}
        _params = kwargs.pop("params", {}) or {}

        cls: ClsType[Union[_models.SessionKillResult, Any]] = kwargs.pop("cls", None)

        _request = build_conduitkernel_kill_session_request(
            session_id=session_id,
            headers=_headers,
            params=_params,
        )
        path_format_arguments = {
            "endpoint": self._serialize.url("self._config.endpoint", self._config.endpoint, "str", skip_quote=True),
        }
        _request.url = self._client.format_url(_request.url, **path_format_arguments)

        _decompress = kwargs.pop("decompress", True)
        _stream = kwargs.pop("stream", False)
        pipeline_response: PipelineResponse = await self._client.pipeline.run(  # type: ignore
            _request, stream=_stream, **kwargs
        )

        response = pipeline_response.http_response

        if response.status_code not in [200]:
            if _stream:
                try:
                    await response.read()  # Load the body in memory and close the socket
                except (StreamConsumedError, StreamClosedError):
                    pass
            map_error(status_code=response.status_code, response=response, error_map=error_map)
            raise HttpResponseError(response=response)

        if _stream:
            deserialized = response.iter_bytes() if _decompress else response.iter_raw()
        else:
            deserialized = _deserialize(Union[_models.SessionKillResult, Any], response.json())

        if cls:
            return cls(pipeline_response, deserialized, {})  # type: ignore

        return deserialized  # type: ignore

    async def get_breaker(self, **kwargs: Any) -> Union[_models.BreakerStateResponse, Any]:
        """Get circuit breaker state.

        :return: BreakerStateResponse or any
        :rtype: ~org.nexus.conduitkernel.models.BreakerStateResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """
        error_map: MutableMapping = {
            401: ClientAuthenticationError,
            404: ResourceNotFoundError,
            409: ResourceExistsError,
            304: ResourceNotModifiedError,
        }
        error_map.update(kwargs.pop("error_map", {}) or {})

        _headers = kwargs.pop("headers", {}) or {}
        _params = kwargs.pop("params", {}) or {}

        cls: ClsType[Union[_models.BreakerStateResponse, Any]] = kwargs.pop("cls", None)

        _request = build_conduitkernel_get_breaker_request(
            headers=_headers,
            params=_params,
        )
        path_format_arguments = {
            "endpoint": self._serialize.url("self._config.endpoint", self._config.endpoint, "str", skip_quote=True),
        }
        _request.url = self._client.format_url(_request.url, **path_format_arguments)

        _decompress = kwargs.pop("decompress", True)
        _stream = kwargs.pop("stream", False)
        pipeline_response: PipelineResponse = await self._client.pipeline.run(  # type: ignore
            _request, stream=_stream, **kwargs
        )

        response = pipeline_response.http_response

        if response.status_code not in [200]:
            if _stream:
                try:
                    await response.read()  # Load the body in memory and close the socket
                except (StreamConsumedError, StreamClosedError):
                    pass
            map_error(status_code=response.status_code, response=response, error_map=error_map)
            raise HttpResponseError(response=response)

        if _stream:
            deserialized = response.iter_bytes() if _decompress else response.iter_raw()
        else:
            deserialized = _deserialize(Union[_models.BreakerStateResponse, Any], response.json())

        if cls:
            return cls(pipeline_response, deserialized, {})  # type: ignore

        return deserialized  # type: ignore

    @overload
    async def trip_breaker(
        self, body: _models.BreakerTripRequest, *, content_type: str = "application/json", **kwargs: Any
    ) -> Union[_models.BreakerStateResponse, Any]:
        """Trip the circuit breaker.

        :param body: Required.
        :type body: ~org.nexus.conduitkernel.models.BreakerTripRequest
        :keyword content_type: Body Parameter content-type. Content type parameter for JSON body.
         Default value is "application/json".
        :paramtype content_type: str
        :return: BreakerStateResponse or any
        :rtype: ~org.nexus.conduitkernel.models.BreakerStateResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """

    @overload
    async def trip_breaker(
        self, body: JSON, *, content_type: str = "application/json", **kwargs: Any
    ) -> Union[_models.BreakerStateResponse, Any]:
        """Trip the circuit breaker.

        :param body: Required.
        :type body: JSON
        :keyword content_type: Body Parameter content-type. Content type parameter for JSON body.
         Default value is "application/json".
        :paramtype content_type: str
        :return: BreakerStateResponse or any
        :rtype: ~org.nexus.conduitkernel.models.BreakerStateResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """

    @overload
    async def trip_breaker(
        self, body: IO[bytes], *, content_type: str = "application/json", **kwargs: Any
    ) -> Union[_models.BreakerStateResponse, Any]:
        """Trip the circuit breaker.

        :param body: Required.
        :type body: IO[bytes]
        :keyword content_type: Body Parameter content-type. Content type parameter for binary body.
         Default value is "application/json".
        :paramtype content_type: str
        :return: BreakerStateResponse or any
        :rtype: ~org.nexus.conduitkernel.models.BreakerStateResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """

    async def trip_breaker(
        self, body: Union[_models.BreakerTripRequest, JSON, IO[bytes]], **kwargs: Any
    ) -> Union[_models.BreakerStateResponse, Any]:
        """Trip the circuit breaker.

        :param body: Is one of the following types: BreakerTripRequest, JSON, IO[bytes] Required.
        :type body: ~org.nexus.conduitkernel.models.BreakerTripRequest or JSON or IO[bytes]
        :return: BreakerStateResponse or any
        :rtype: ~org.nexus.conduitkernel.models.BreakerStateResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """
        error_map: MutableMapping = {
            401: ClientAuthenticationError,
            404: ResourceNotFoundError,
            409: ResourceExistsError,
            304: ResourceNotModifiedError,
        }
        error_map.update(kwargs.pop("error_map", {}) or {})

        _headers = case_insensitive_dict(kwargs.pop("headers", {}) or {})
        _params = kwargs.pop("params", {}) or {}

        content_type: Optional[str] = kwargs.pop("content_type", _headers.pop("Content-Type", None))
        cls: ClsType[Union[_models.BreakerStateResponse, Any]] = kwargs.pop("cls", None)

        content_type = content_type or "application/json"
        _content = None
        if isinstance(body, (IOBase, bytes)):
            _content = body
        else:
            _content = json.dumps(body, cls=SdkJSONEncoder, exclude_readonly=True)  # type: ignore

        _request = build_conduitkernel_trip_breaker_request(
            content_type=content_type,
            content=_content,
            headers=_headers,
            params=_params,
        )
        path_format_arguments = {
            "endpoint": self._serialize.url("self._config.endpoint", self._config.endpoint, "str", skip_quote=True),
        }
        _request.url = self._client.format_url(_request.url, **path_format_arguments)

        _decompress = kwargs.pop("decompress", True)
        _stream = kwargs.pop("stream", False)
        pipeline_response: PipelineResponse = await self._client.pipeline.run(  # type: ignore
            _request, stream=_stream, **kwargs
        )

        response = pipeline_response.http_response

        if response.status_code not in [200]:
            if _stream:
                try:
                    await response.read()  # Load the body in memory and close the socket
                except (StreamConsumedError, StreamClosedError):
                    pass
            map_error(status_code=response.status_code, response=response, error_map=error_map)
            raise HttpResponseError(response=response)

        if _stream:
            deserialized = response.iter_bytes() if _decompress else response.iter_raw()
        else:
            deserialized = _deserialize(Union[_models.BreakerStateResponse, Any], response.json())

        if cls:
            return cls(pipeline_response, deserialized, {})  # type: ignore

        return deserialized  # type: ignore

    async def reset_breaker(self, **kwargs: Any) -> Union[_models.BreakerStateResponse, Any]:
        """Reset the circuit breaker.

        :return: BreakerStateResponse or any
        :rtype: ~org.nexus.conduitkernel.models.BreakerStateResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """
        error_map: MutableMapping = {
            401: ClientAuthenticationError,
            404: ResourceNotFoundError,
            409: ResourceExistsError,
            304: ResourceNotModifiedError,
        }
        error_map.update(kwargs.pop("error_map", {}) or {})

        _headers = kwargs.pop("headers", {}) or {}
        _params = kwargs.pop("params", {}) or {}

        cls: ClsType[Union[_models.BreakerStateResponse, Any]] = kwargs.pop("cls", None)

        _request = build_conduitkernel_reset_breaker_request(
            headers=_headers,
            params=_params,
        )
        path_format_arguments = {
            "endpoint": self._serialize.url("self._config.endpoint", self._config.endpoint, "str", skip_quote=True),
        }
        _request.url = self._client.format_url(_request.url, **path_format_arguments)

        _decompress = kwargs.pop("decompress", True)
        _stream = kwargs.pop("stream", False)
        pipeline_response: PipelineResponse = await self._client.pipeline.run(  # type: ignore
            _request, stream=_stream, **kwargs
        )

        response = pipeline_response.http_response

        if response.status_code not in [200]:
            if _stream:
                try:
                    await response.read()  # Load the body in memory and close the socket
                except (StreamConsumedError, StreamClosedError):
                    pass
            map_error(status_code=response.status_code, response=response, error_map=error_map)
            raise HttpResponseError(response=response)

        if _stream:
            deserialized = response.iter_bytes() if _decompress else response.iter_raw()
        else:
            deserialized = _deserialize(Union[_models.BreakerStateResponse, Any], response.json())

        if cls:
            return cls(pipeline_response, deserialized, {})  # type: ignore

        return deserialized  # type: ignore

    async def pause_breaker(self, **kwargs: Any) -> Union[_models.BreakerStateResponse, Any]:
        """Pause the circuit breaker.

        :return: BreakerStateResponse or any
        :rtype: ~org.nexus.conduitkernel.models.BreakerStateResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """
        error_map: MutableMapping = {
            401: ClientAuthenticationError,
            404: ResourceNotFoundError,
            409: ResourceExistsError,
            304: ResourceNotModifiedError,
        }
        error_map.update(kwargs.pop("error_map", {}) or {})

        _headers = kwargs.pop("headers", {}) or {}
        _params = kwargs.pop("params", {}) or {}

        cls: ClsType[Union[_models.BreakerStateResponse, Any]] = kwargs.pop("cls", None)

        _request = build_conduitkernel_pause_breaker_request(
            headers=_headers,
            params=_params,
        )
        path_format_arguments = {
            "endpoint": self._serialize.url("self._config.endpoint", self._config.endpoint, "str", skip_quote=True),
        }
        _request.url = self._client.format_url(_request.url, **path_format_arguments)

        _decompress = kwargs.pop("decompress", True)
        _stream = kwargs.pop("stream", False)
        pipeline_response: PipelineResponse = await self._client.pipeline.run(  # type: ignore
            _request, stream=_stream, **kwargs
        )

        response = pipeline_response.http_response

        if response.status_code not in [200]:
            if _stream:
                try:
                    await response.read()  # Load the body in memory and close the socket
                except (StreamConsumedError, StreamClosedError):
                    pass
            map_error(status_code=response.status_code, response=response, error_map=error_map)
            raise HttpResponseError(response=response)

        if _stream:
            deserialized = response.iter_bytes() if _decompress else response.iter_raw()
        else:
            deserialized = _deserialize(Union[_models.BreakerStateResponse, Any], response.json())

        if cls:
            return cls(pipeline_response, deserialized, {})  # type: ignore

        return deserialized  # type: ignore

    async def resume_breaker(self, **kwargs: Any) -> Union[_models.BreakerStateResponse, Any]:
        """Resume the circuit breaker.

        :return: BreakerStateResponse or any
        :rtype: ~org.nexus.conduitkernel.models.BreakerStateResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """
        error_map: MutableMapping = {
            401: ClientAuthenticationError,
            404: ResourceNotFoundError,
            409: ResourceExistsError,
            304: ResourceNotModifiedError,
        }
        error_map.update(kwargs.pop("error_map", {}) or {})

        _headers = kwargs.pop("headers", {}) or {}
        _params = kwargs.pop("params", {}) or {}

        cls: ClsType[Union[_models.BreakerStateResponse, Any]] = kwargs.pop("cls", None)

        _request = build_conduitkernel_resume_breaker_request(
            headers=_headers,
            params=_params,
        )
        path_format_arguments = {
            "endpoint": self._serialize.url("self._config.endpoint", self._config.endpoint, "str", skip_quote=True),
        }
        _request.url = self._client.format_url(_request.url, **path_format_arguments)

        _decompress = kwargs.pop("decompress", True)
        _stream = kwargs.pop("stream", False)
        pipeline_response: PipelineResponse = await self._client.pipeline.run(  # type: ignore
            _request, stream=_stream, **kwargs
        )

        response = pipeline_response.http_response

        if response.status_code not in [200]:
            if _stream:
                try:
                    await response.read()  # Load the body in memory and close the socket
                except (StreamConsumedError, StreamClosedError):
                    pass
            map_error(status_code=response.status_code, response=response, error_map=error_map)
            raise HttpResponseError(response=response)

        if _stream:
            deserialized = response.iter_bytes() if _decompress else response.iter_raw()
        else:
            deserialized = _deserialize(Union[_models.BreakerStateResponse, Any], response.json())

        if cls:
            return cls(pipeline_response, deserialized, {})  # type: ignore

        return deserialized  # type: ignore

    async def get_failure_recovery(self, **kwargs: Any) -> Union[_models.BreakerFailureRecoveryConfig, Any]:
        """Get failure-recovery configuration.

        :return: BreakerFailureRecoveryConfig or any
        :rtype: ~org.nexus.conduitkernel.models.BreakerFailureRecoveryConfig or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """
        error_map: MutableMapping = {
            401: ClientAuthenticationError,
            404: ResourceNotFoundError,
            409: ResourceExistsError,
            304: ResourceNotModifiedError,
        }
        error_map.update(kwargs.pop("error_map", {}) or {})

        _headers = kwargs.pop("headers", {}) or {}
        _params = kwargs.pop("params", {}) or {}

        cls: ClsType[Union[_models.BreakerFailureRecoveryConfig, Any]] = kwargs.pop("cls", None)

        _request = build_conduitkernel_get_failure_recovery_request(
            headers=_headers,
            params=_params,
        )
        path_format_arguments = {
            "endpoint": self._serialize.url("self._config.endpoint", self._config.endpoint, "str", skip_quote=True),
        }
        _request.url = self._client.format_url(_request.url, **path_format_arguments)

        _decompress = kwargs.pop("decompress", True)
        _stream = kwargs.pop("stream", False)
        pipeline_response: PipelineResponse = await self._client.pipeline.run(  # type: ignore
            _request, stream=_stream, **kwargs
        )

        response = pipeline_response.http_response

        if response.status_code not in [200]:
            if _stream:
                try:
                    await response.read()  # Load the body in memory and close the socket
                except (StreamConsumedError, StreamClosedError):
                    pass
            map_error(status_code=response.status_code, response=response, error_map=error_map)
            raise HttpResponseError(response=response)

        if _stream:
            deserialized = response.iter_bytes() if _decompress else response.iter_raw()
        else:
            deserialized = _deserialize(Union[_models.BreakerFailureRecoveryConfig, Any], response.json())

        if cls:
            return cls(pipeline_response, deserialized, {})  # type: ignore

        return deserialized  # type: ignore

    @overload
    async def save_failure_recovery(
        self, body: _models.BreakerFailureRecoveryConfig, *, content_type: str = "application/json", **kwargs: Any
    ) -> Union[_models.BreakerFailureRecoveryConfig, Any]:
        """Save failure-recovery configuration.

        :param body: Required.
        :type body: ~org.nexus.conduitkernel.models.BreakerFailureRecoveryConfig
        :keyword content_type: Body Parameter content-type. Content type parameter for JSON body.
         Default value is "application/json".
        :paramtype content_type: str
        :return: BreakerFailureRecoveryConfig or any
        :rtype: ~org.nexus.conduitkernel.models.BreakerFailureRecoveryConfig or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """

    @overload
    async def save_failure_recovery(
        self, body: JSON, *, content_type: str = "application/json", **kwargs: Any
    ) -> Union[_models.BreakerFailureRecoveryConfig, Any]:
        """Save failure-recovery configuration.

        :param body: Required.
        :type body: JSON
        :keyword content_type: Body Parameter content-type. Content type parameter for JSON body.
         Default value is "application/json".
        :paramtype content_type: str
        :return: BreakerFailureRecoveryConfig or any
        :rtype: ~org.nexus.conduitkernel.models.BreakerFailureRecoveryConfig or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """

    @overload
    async def save_failure_recovery(
        self, body: IO[bytes], *, content_type: str = "application/json", **kwargs: Any
    ) -> Union[_models.BreakerFailureRecoveryConfig, Any]:
        """Save failure-recovery configuration.

        :param body: Required.
        :type body: IO[bytes]
        :keyword content_type: Body Parameter content-type. Content type parameter for binary body.
         Default value is "application/json".
        :paramtype content_type: str
        :return: BreakerFailureRecoveryConfig or any
        :rtype: ~org.nexus.conduitkernel.models.BreakerFailureRecoveryConfig or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """

    async def save_failure_recovery(
        self, body: Union[_models.BreakerFailureRecoveryConfig, JSON, IO[bytes]], **kwargs: Any
    ) -> Union[_models.BreakerFailureRecoveryConfig, Any]:
        """Save failure-recovery configuration.

        :param body: Is one of the following types: BreakerFailureRecoveryConfig, JSON, IO[bytes]
         Required.
        :type body: ~org.nexus.conduitkernel.models.BreakerFailureRecoveryConfig or JSON or IO[bytes]
        :return: BreakerFailureRecoveryConfig or any
        :rtype: ~org.nexus.conduitkernel.models.BreakerFailureRecoveryConfig or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """
        error_map: MutableMapping = {
            401: ClientAuthenticationError,
            404: ResourceNotFoundError,
            409: ResourceExistsError,
            304: ResourceNotModifiedError,
        }
        error_map.update(kwargs.pop("error_map", {}) or {})

        _headers = case_insensitive_dict(kwargs.pop("headers", {}) or {})
        _params = kwargs.pop("params", {}) or {}

        content_type: Optional[str] = kwargs.pop("content_type", _headers.pop("Content-Type", None))
        cls: ClsType[Union[_models.BreakerFailureRecoveryConfig, Any]] = kwargs.pop("cls", None)

        content_type = content_type or "application/json"
        _content = None
        if isinstance(body, (IOBase, bytes)):
            _content = body
        else:
            _content = json.dumps(body, cls=SdkJSONEncoder, exclude_readonly=True)  # type: ignore

        _request = build_conduitkernel_save_failure_recovery_request(
            content_type=content_type,
            content=_content,
            headers=_headers,
            params=_params,
        )
        path_format_arguments = {
            "endpoint": self._serialize.url("self._config.endpoint", self._config.endpoint, "str", skip_quote=True),
        }
        _request.url = self._client.format_url(_request.url, **path_format_arguments)

        _decompress = kwargs.pop("decompress", True)
        _stream = kwargs.pop("stream", False)
        pipeline_response: PipelineResponse = await self._client.pipeline.run(  # type: ignore
            _request, stream=_stream, **kwargs
        )

        response = pipeline_response.http_response

        if response.status_code not in [200]:
            if _stream:
                try:
                    await response.read()  # Load the body in memory and close the socket
                except (StreamConsumedError, StreamClosedError):
                    pass
            map_error(status_code=response.status_code, response=response, error_map=error_map)
            raise HttpResponseError(response=response)

        if _stream:
            deserialized = response.iter_bytes() if _decompress else response.iter_raw()
        else:
            deserialized = _deserialize(Union[_models.BreakerFailureRecoveryConfig, Any], response.json())

        if cls:
            return cls(pipeline_response, deserialized, {})  # type: ignore

        return deserialized  # type: ignore

    async def list_identities(self, **kwargs: Any) -> Union[_models.IdentityListResponse, Any]:
        """List identities.

        :return: IdentityListResponse or any
        :rtype: ~org.nexus.conduitkernel.models.IdentityListResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """
        error_map: MutableMapping = {
            401: ClientAuthenticationError,
            404: ResourceNotFoundError,
            409: ResourceExistsError,
            304: ResourceNotModifiedError,
        }
        error_map.update(kwargs.pop("error_map", {}) or {})

        _headers = kwargs.pop("headers", {}) or {}
        _params = kwargs.pop("params", {}) or {}

        cls: ClsType[Union[_models.IdentityListResponse, Any]] = kwargs.pop("cls", None)

        _request = build_conduitkernel_list_identities_request(
            headers=_headers,
            params=_params,
        )
        path_format_arguments = {
            "endpoint": self._serialize.url("self._config.endpoint", self._config.endpoint, "str", skip_quote=True),
        }
        _request.url = self._client.format_url(_request.url, **path_format_arguments)

        _decompress = kwargs.pop("decompress", True)
        _stream = kwargs.pop("stream", False)
        pipeline_response: PipelineResponse = await self._client.pipeline.run(  # type: ignore
            _request, stream=_stream, **kwargs
        )

        response = pipeline_response.http_response

        if response.status_code not in [200]:
            if _stream:
                try:
                    await response.read()  # Load the body in memory and close the socket
                except (StreamConsumedError, StreamClosedError):
                    pass
            map_error(status_code=response.status_code, response=response, error_map=error_map)
            raise HttpResponseError(response=response)

        if _stream:
            deserialized = response.iter_bytes() if _decompress else response.iter_raw()
        else:
            deserialized = _deserialize(Union[_models.IdentityListResponse, Any], response.json())

        if cls:
            return cls(pipeline_response, deserialized, {})  # type: ignore

        return deserialized  # type: ignore

    async def update_identity(
        self, identity_id: str, body: Any, **kwargs: Any
    ) -> Union[_models.IdentityUpdateResponse, Any]:
        """Update an identity.

        :param identity_id: Required.
        :type identity_id: str
        :param body: Required.
        :type body: any
        :return: IdentityUpdateResponse or any
        :rtype: ~org.nexus.conduitkernel.models.IdentityUpdateResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """
        error_map: MutableMapping = {
            401: ClientAuthenticationError,
            404: ResourceNotFoundError,
            409: ResourceExistsError,
            304: ResourceNotModifiedError,
        }
        error_map.update(kwargs.pop("error_map", {}) or {})

        _headers = case_insensitive_dict(kwargs.pop("headers", {}) or {})
        _params = kwargs.pop("params", {}) or {}

        content_type: str = kwargs.pop("content_type", _headers.pop("Content-Type", "application/json"))
        cls: ClsType[Union[_models.IdentityUpdateResponse, Any]] = kwargs.pop("cls", None)

        _content = json.dumps(body, cls=SdkJSONEncoder, exclude_readonly=True)  # type: ignore

        _request = build_conduitkernel_update_identity_request(
            identity_id=identity_id,
            content_type=content_type,
            content=_content,
            headers=_headers,
            params=_params,
        )
        path_format_arguments = {
            "endpoint": self._serialize.url("self._config.endpoint", self._config.endpoint, "str", skip_quote=True),
        }
        _request.url = self._client.format_url(_request.url, **path_format_arguments)

        _decompress = kwargs.pop("decompress", True)
        _stream = kwargs.pop("stream", False)
        pipeline_response: PipelineResponse = await self._client.pipeline.run(  # type: ignore
            _request, stream=_stream, **kwargs
        )

        response = pipeline_response.http_response

        if response.status_code not in [200]:
            if _stream:
                try:
                    await response.read()  # Load the body in memory and close the socket
                except (StreamConsumedError, StreamClosedError):
                    pass
            map_error(status_code=response.status_code, response=response, error_map=error_map)
            raise HttpResponseError(response=response)

        if _stream:
            deserialized = response.iter_bytes() if _decompress else response.iter_raw()
        else:
            deserialized = _deserialize(Union[_models.IdentityUpdateResponse, Any], response.json())

        if cls:
            return cls(pipeline_response, deserialized, {})  # type: ignore

        return deserialized  # type: ignore

    async def delete_identity(self, identity_id: str, **kwargs: Any) -> Any:
        """Delete an identity.

        :param identity_id: Required.
        :type identity_id: str
        :return: any
        :rtype: any
        :raises ~corehttp.exceptions.HttpResponseError:
        """
        error_map: MutableMapping = {
            401: ClientAuthenticationError,
            404: ResourceNotFoundError,
            409: ResourceExistsError,
            304: ResourceNotModifiedError,
        }
        error_map.update(kwargs.pop("error_map", {}) or {})

        _headers = kwargs.pop("headers", {}) or {}
        _params = kwargs.pop("params", {}) or {}

        cls: ClsType[Any] = kwargs.pop("cls", None)

        _request = build_conduitkernel_delete_identity_request(
            identity_id=identity_id,
            headers=_headers,
            params=_params,
        )
        path_format_arguments = {
            "endpoint": self._serialize.url("self._config.endpoint", self._config.endpoint, "str", skip_quote=True),
        }
        _request.url = self._client.format_url(_request.url, **path_format_arguments)

        _decompress = kwargs.pop("decompress", True)
        _stream = kwargs.pop("stream", False)
        pipeline_response: PipelineResponse = await self._client.pipeline.run(  # type: ignore
            _request, stream=_stream, **kwargs
        )

        response = pipeline_response.http_response

        if response.status_code not in [200]:
            if _stream:
                try:
                    await response.read()  # Load the body in memory and close the socket
                except (StreamConsumedError, StreamClosedError):
                    pass
            map_error(status_code=response.status_code, response=response, error_map=error_map)
            raise HttpResponseError(response=response)

        if _stream:
            deserialized = response.iter_bytes() if _decompress else response.iter_raw()
        else:
            deserialized = _deserialize(Any, response.json())

        if cls:
            return cls(pipeline_response, deserialized, {})  # type: ignore

        return deserialized  # type: ignore

    async def check_consistency(self, **kwargs: Any) -> Union[_models.ConsistencyCheckResponse, Any]:
        """Run a consistency check.

        :return: ConsistencyCheckResponse or any
        :rtype: ~org.nexus.conduitkernel.models.ConsistencyCheckResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """
        error_map: MutableMapping = {
            401: ClientAuthenticationError,
            404: ResourceNotFoundError,
            409: ResourceExistsError,
            304: ResourceNotModifiedError,
        }
        error_map.update(kwargs.pop("error_map", {}) or {})

        _headers = kwargs.pop("headers", {}) or {}
        _params = kwargs.pop("params", {}) or {}

        cls: ClsType[Union[_models.ConsistencyCheckResponse, Any]] = kwargs.pop("cls", None)

        _request = build_conduitkernel_check_consistency_request(
            headers=_headers,
            params=_params,
        )
        path_format_arguments = {
            "endpoint": self._serialize.url("self._config.endpoint", self._config.endpoint, "str", skip_quote=True),
        }
        _request.url = self._client.format_url(_request.url, **path_format_arguments)

        _decompress = kwargs.pop("decompress", True)
        _stream = kwargs.pop("stream", False)
        pipeline_response: PipelineResponse = await self._client.pipeline.run(  # type: ignore
            _request, stream=_stream, **kwargs
        )

        response = pipeline_response.http_response

        if response.status_code not in [200]:
            if _stream:
                try:
                    await response.read()  # Load the body in memory and close the socket
                except (StreamConsumedError, StreamClosedError):
                    pass
            map_error(status_code=response.status_code, response=response, error_map=error_map)
            raise HttpResponseError(response=response)

        if _stream:
            deserialized = response.iter_bytes() if _decompress else response.iter_raw()
        else:
            deserialized = _deserialize(Union[_models.ConsistencyCheckResponse, Any], response.json())

        if cls:
            return cls(pipeline_response, deserialized, {})  # type: ignore

        return deserialized  # type: ignore

    @overload
    async def apply_delta(
        self, body: _models.DeltaApplyRequest, *, content_type: str = "application/json", **kwargs: Any
    ) -> Union[_models.DeltaResponse, Any]:
        """Apply a delta.

        :param body: Required.
        :type body: ~org.nexus.conduitkernel.models.DeltaApplyRequest
        :keyword content_type: Body Parameter content-type. Content type parameter for JSON body.
         Default value is "application/json".
        :paramtype content_type: str
        :return: DeltaResponse or any
        :rtype: ~org.nexus.conduitkernel.models.DeltaResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """

    @overload
    async def apply_delta(
        self, body: JSON, *, content_type: str = "application/json", **kwargs: Any
    ) -> Union[_models.DeltaResponse, Any]:
        """Apply a delta.

        :param body: Required.
        :type body: JSON
        :keyword content_type: Body Parameter content-type. Content type parameter for JSON body.
         Default value is "application/json".
        :paramtype content_type: str
        :return: DeltaResponse or any
        :rtype: ~org.nexus.conduitkernel.models.DeltaResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """

    @overload
    async def apply_delta(
        self, body: IO[bytes], *, content_type: str = "application/json", **kwargs: Any
    ) -> Union[_models.DeltaResponse, Any]:
        """Apply a delta.

        :param body: Required.
        :type body: IO[bytes]
        :keyword content_type: Body Parameter content-type. Content type parameter for binary body.
         Default value is "application/json".
        :paramtype content_type: str
        :return: DeltaResponse or any
        :rtype: ~org.nexus.conduitkernel.models.DeltaResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """

    async def apply_delta(
        self, body: Union[_models.DeltaApplyRequest, JSON, IO[bytes]], **kwargs: Any
    ) -> Union[_models.DeltaResponse, Any]:
        """Apply a delta.

        :param body: Is one of the following types: DeltaApplyRequest, JSON, IO[bytes] Required.
        :type body: ~org.nexus.conduitkernel.models.DeltaApplyRequest or JSON or IO[bytes]
        :return: DeltaResponse or any
        :rtype: ~org.nexus.conduitkernel.models.DeltaResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """
        error_map: MutableMapping = {
            401: ClientAuthenticationError,
            404: ResourceNotFoundError,
            409: ResourceExistsError,
            304: ResourceNotModifiedError,
        }
        error_map.update(kwargs.pop("error_map", {}) or {})

        _headers = case_insensitive_dict(kwargs.pop("headers", {}) or {})
        _params = kwargs.pop("params", {}) or {}

        content_type: Optional[str] = kwargs.pop("content_type", _headers.pop("Content-Type", None))
        cls: ClsType[Union[_models.DeltaResponse, Any]] = kwargs.pop("cls", None)

        content_type = content_type or "application/json"
        _content = None
        if isinstance(body, (IOBase, bytes)):
            _content = body
        else:
            _content = json.dumps(body, cls=SdkJSONEncoder, exclude_readonly=True)  # type: ignore

        _request = build_conduitkernel_apply_delta_request(
            content_type=content_type,
            content=_content,
            headers=_headers,
            params=_params,
        )
        path_format_arguments = {
            "endpoint": self._serialize.url("self._config.endpoint", self._config.endpoint, "str", skip_quote=True),
        }
        _request.url = self._client.format_url(_request.url, **path_format_arguments)

        _decompress = kwargs.pop("decompress", True)
        _stream = kwargs.pop("stream", False)
        pipeline_response: PipelineResponse = await self._client.pipeline.run(  # type: ignore
            _request, stream=_stream, **kwargs
        )

        response = pipeline_response.http_response

        if response.status_code not in [200]:
            if _stream:
                try:
                    await response.read()  # Load the body in memory and close the socket
                except (StreamConsumedError, StreamClosedError):
                    pass
            map_error(status_code=response.status_code, response=response, error_map=error_map)
            raise HttpResponseError(response=response)

        if _stream:
            deserialized = response.iter_bytes() if _decompress else response.iter_raw()
        else:
            deserialized = _deserialize(Union[_models.DeltaResponse, Any], response.json())

        if cls:
            return cls(pipeline_response, deserialized, {})  # type: ignore

        return deserialized  # type: ignore

    async def get_delta_state(self, **kwargs: Any) -> Union[_models.StateSummaryResponse, Any]:
        """Get delta-derived state summary.

        :return: StateSummaryResponse or any
        :rtype: ~org.nexus.conduitkernel.models.StateSummaryResponse or any
        :raises ~corehttp.exceptions.HttpResponseError:
        """
        error_map: MutableMapping = {
            401: ClientAuthenticationError,
            404: ResourceNotFoundError,
            409: ResourceExistsError,
            304: ResourceNotModifiedError,
        }
        error_map.update(kwargs.pop("error_map", {}) or {})

        _headers = kwargs.pop("headers", {}) or {}
        _params = kwargs.pop("params", {}) or {}

        cls: ClsType[Union[_models.StateSummaryResponse, Any]] = kwargs.pop("cls", None)

        _request = build_conduitkernel_get_delta_state_request(
            headers=_headers,
            params=_params,
        )
        path_format_arguments = {
            "endpoint": self._serialize.url("self._config.endpoint", self._config.endpoint, "str", skip_quote=True),
        }
        _request.url = self._client.format_url(_request.url, **path_format_arguments)

        _decompress = kwargs.pop("decompress", True)
        _stream = kwargs.pop("stream", False)
        pipeline_response: PipelineResponse = await self._client.pipeline.run(  # type: ignore
            _request, stream=_stream, **kwargs
        )

        response = pipeline_response.http_response

        if response.status_code not in [200]:
            if _stream:
                try:
                    await response.read()  # Load the body in memory and close the socket
                except (StreamConsumedError, StreamClosedError):
                    pass
            map_error(status_code=response.status_code, response=response, error_map=error_map)
            raise HttpResponseError(response=response)

        if _stream:
            deserialized = response.iter_bytes() if _decompress else response.iter_raw()
        else:
            deserialized = _deserialize(Union[_models.StateSummaryResponse, Any], response.json())

        if cls:
            return cls(pipeline_response, deserialized, {})  # type: ignore

        return deserialized  # type: ignore
