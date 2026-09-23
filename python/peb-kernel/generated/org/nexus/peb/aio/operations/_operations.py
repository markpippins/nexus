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
from ..._utils.model_base import SdkJSONEncoder, _deserialize
from ..._utils.serialization import Deserializer, Serializer
from ...operations._operations import (
    build_peb_health_endpoint_health_request,
    build_peb_transaction_endpoint_submit_request,
)
from .._configuration import pebClientConfiguration

JSON = MutableMapping[str, Any]
T = TypeVar("T")
ClsType = Optional[Callable[[PipelineResponse[HttpRequest, AsyncHttpResponse], T, dict[str, Any]], Any]]


class PebTransactionEndpointOperations:
    """
    .. warning::
        **DO NOT** instantiate this class directly.

        Instead, you should access the following operations through
        :class:`~org.nexus.peb.aio.pebClient`'s
        :attr:`peb_transaction_endpoint` attribute.
    """

    def __init__(self, *args, **kwargs) -> None:
        input_args = list(args)
        self._client: AsyncPipelineClient = input_args.pop(0) if input_args else kwargs.pop("client")
        self._config: pebClientConfiguration = input_args.pop(0) if input_args else kwargs.pop("config")
        self._serialize: Serializer = input_args.pop(0) if input_args else kwargs.pop("serializer")
        self._deserialize: Deserializer = input_args.pop(0) if input_args else kwargs.pop("deserializer")

    @overload
    async def submit(
        self, request: _models.PebTransactionRequest, *, content_type: str = "application/json", **kwargs: Any
    ) -> _models.PebAdmissionResult:
        """Submit a transaction to the PEB governance engine.
          The toolName field determines which admission path and engine is invoked:

        * peb_validate_transition / peb_check_invariants / peb_validate_transform → VALIDATE path
        * peb_record_decision / peb_append_trace_segment / peb_request_clarification /
        peb_extension_proposal → MUTATE path
        * peb_report_violation → REPORT_VIOLATION path (always rejected, recorded as violation)
        * unknown toolName → the classifier reports UNKNOWN; structural validation rejects it with HTTP
        422.

        :param request: Required.
        :type request: ~org.nexus.peb.models.PebTransactionRequest
        :keyword content_type: Body Parameter content-type. Content type parameter for JSON body.
         Default value is "application/json".
        :paramtype content_type: str
        :return: PebAdmissionResult. The PebAdmissionResult is compatible with MutableMapping
        :rtype: ~org.nexus.peb.models.PebAdmissionResult
        :raises ~corehttp.exceptions.HttpResponseError:
        """

    @overload
    async def submit(
        self, request: JSON, *, content_type: str = "application/json", **kwargs: Any
    ) -> _models.PebAdmissionResult:
        """Submit a transaction to the PEB governance engine.
          The toolName field determines which admission path and engine is invoked:

        * peb_validate_transition / peb_check_invariants / peb_validate_transform → VALIDATE path
        * peb_record_decision / peb_append_trace_segment / peb_request_clarification /
        peb_extension_proposal → MUTATE path
        * peb_report_violation → REPORT_VIOLATION path (always rejected, recorded as violation)
        * unknown toolName → the classifier reports UNKNOWN; structural validation rejects it with HTTP
        422.

        :param request: Required.
        :type request: JSON
        :keyword content_type: Body Parameter content-type. Content type parameter for JSON body.
         Default value is "application/json".
        :paramtype content_type: str
        :return: PebAdmissionResult. The PebAdmissionResult is compatible with MutableMapping
        :rtype: ~org.nexus.peb.models.PebAdmissionResult
        :raises ~corehttp.exceptions.HttpResponseError:
        """

    @overload
    async def submit(
        self, request: IO[bytes], *, content_type: str = "application/json", **kwargs: Any
    ) -> _models.PebAdmissionResult:
        """Submit a transaction to the PEB governance engine.
          The toolName field determines which admission path and engine is invoked:

        * peb_validate_transition / peb_check_invariants / peb_validate_transform → VALIDATE path
        * peb_record_decision / peb_append_trace_segment / peb_request_clarification /
        peb_extension_proposal → MUTATE path
        * peb_report_violation → REPORT_VIOLATION path (always rejected, recorded as violation)
        * unknown toolName → the classifier reports UNKNOWN; structural validation rejects it with HTTP
        422.

        :param request: Required.
        :type request: IO[bytes]
        :keyword content_type: Body Parameter content-type. Content type parameter for binary body.
         Default value is "application/json".
        :paramtype content_type: str
        :return: PebAdmissionResult. The PebAdmissionResult is compatible with MutableMapping
        :rtype: ~org.nexus.peb.models.PebAdmissionResult
        :raises ~corehttp.exceptions.HttpResponseError:
        """

    async def submit(
        self, request: Union[_models.PebTransactionRequest, JSON, IO[bytes]], **kwargs: Any
    ) -> _models.PebAdmissionResult:
        """Submit a transaction to the PEB governance engine.
          The toolName field determines which admission path and engine is invoked:

        * peb_validate_transition / peb_check_invariants / peb_validate_transform → VALIDATE path
        * peb_record_decision / peb_append_trace_segment / peb_request_clarification /
        peb_extension_proposal → MUTATE path
        * peb_report_violation → REPORT_VIOLATION path (always rejected, recorded as violation)
        * unknown toolName → the classifier reports UNKNOWN; structural validation rejects it with HTTP
        422.

        :param request: Is one of the following types: PebTransactionRequest, JSON, IO[bytes] Required.
        :type request: ~org.nexus.peb.models.PebTransactionRequest or JSON or IO[bytes]
        :return: PebAdmissionResult. The PebAdmissionResult is compatible with MutableMapping
        :rtype: ~org.nexus.peb.models.PebAdmissionResult
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
        cls: ClsType[_models.PebAdmissionResult] = kwargs.pop("cls", None)

        content_type = content_type or "application/json"
        _content = None
        if isinstance(request, (IOBase, bytes)):
            _content = request
        else:
            _content = json.dumps(request, cls=SdkJSONEncoder, exclude_readonly=True)  # type: ignore

        _request = build_peb_transaction_endpoint_submit_request(
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
        pipeline_response: PipelineResponse = await self._client.pipeline.run(_request, stream=_stream, **kwargs)

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
            deserialized = _deserialize(_models.PebAdmissionResult, response.json())

        if cls:
            return cls(pipeline_response, deserialized, {})  # type: ignore

        return deserialized  # type: ignore


class PebHealthEndpointOperations:
    """
    .. warning::
        **DO NOT** instantiate this class directly.

        Instead, you should access the following operations through
        :class:`~org.nexus.peb.aio.pebClient`'s
        :attr:`peb_health_endpoint` attribute.
    """

    def __init__(self, *args, **kwargs) -> None:
        input_args = list(args)
        self._client: AsyncPipelineClient = input_args.pop(0) if input_args else kwargs.pop("client")
        self._config: pebClientConfiguration = input_args.pop(0) if input_args else kwargs.pop("config")
        self._serialize: Serializer = input_args.pop(0) if input_args else kwargs.pop("serializer")
        self._deserialize: Deserializer = input_args.pop(0) if input_args else kwargs.pop("deserializer")

    async def health(self, **kwargs: Any) -> _models.PebHealthResponse:
        """health.

        :return: PebHealthResponse. The PebHealthResponse is compatible with MutableMapping
        :rtype: ~org.nexus.peb.models.PebHealthResponse
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

        cls: ClsType[_models.PebHealthResponse] = kwargs.pop("cls", None)

        _request = build_peb_health_endpoint_health_request(
            headers=_headers,
            params=_params,
        )
        path_format_arguments = {
            "endpoint": self._serialize.url("self._config.endpoint", self._config.endpoint, "str", skip_quote=True),
        }
        _request.url = self._client.format_url(_request.url, **path_format_arguments)

        _decompress = kwargs.pop("decompress", True)
        _stream = kwargs.pop("stream", False)
        pipeline_response: PipelineResponse = await self._client.pipeline.run(_request, stream=_stream, **kwargs)

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
            deserialized = _deserialize(_models.PebHealthResponse, response.json())

        if cls:
            return cls(pipeline_response, deserialized, {})  # type: ignore

        return deserialized  # type: ignore
