#
# Copyright (c) 2023 Airbyte, Inc., all rights reserved.
#

from dataclasses import InitVar, dataclass, field
from typing import Any, List, Mapping, MutableMapping, Optional, Union

import requests
from airbyte_cdk.models import FailureType
from airbyte_cdk.sources.declarative.requesters.error_handlers.backoff_strategies.exponential_backoff_strategy import (
    ExponentialBackoffStrategy,
)
from airbyte_cdk.sources.declarative.requesters.error_handlers.backoff_strategy import BackoffStrategy
from airbyte_cdk.sources.declarative.requesters.error_handlers.http_response_filter import HttpResponseFilter
from airbyte_cdk.sources.streams.http.error_handlers import ErrorHandler
from airbyte_cdk.sources.streams.http.error_handlers.response_models import (
    DEFAULT_ERROR_RESOLUTION,
    SUCCESS_RESOLUTION,
    ErrorResolution,
    ResponseAction,
)
from airbyte_cdk.sources.types import Config


@dataclass
class DefaultErrorHandler(ErrorHandler):
    """
    Default error handler.

    By default, the handler will only retry server errors (HTTP 5XX) and too many requests (HTTP 429) with exponential backoff.

    If the response is successful, then return SUCCESS
    Otherwise, iterate over the response_filters.
    If any of the filter match the response, then return the appropriate status.
    If the match is RETRY, then iterate sequentially over the backoff_strategies and return the first non-None backoff time.

    Sample configs:

    1. retry 10 times
    `
        error_handler:
          max_retries: 10
    `
    2. backoff for 5 seconds
    `
        error_handler:
          backoff_strategies:
            - type: "ConstantBackoff"
              backoff_time_in_seconds: 5
    `
    3. retry on HTTP 404
    `
        error_handler:
          response_filters:
            - http_codes: [ 404 ]
              action: RETRY
    `
    4. ignore HTTP 404
    `
      error_handler:
        response_filters:
          - http_codes: [ 404 ]
            action: IGNORE
    `
    5. retry if error message contains `retrythisrequest!` substring
    `
        error_handler:
          response_filters:
            - error_message_contain: "retrythisrequest!"
              action: IGNORE
    `
    6. retry if 'code' is a field present in the response body
    `
        error_handler:
          response_filters:
            - predicate: "{{ 'code' in response }}"
              action: IGNORE
    `

    7. ignore 429 and retry on 404
    `
        error_handler:
        - http_codes: [ 429 ]
          action: IGNORE
        - http_codes: [ 404 ]
          action: RETRY
    `

    Attributes:
        response_filters (Optional[List[HttpResponseFilter]]): response filters to iterate on
        max_retries (Optional[int]): maximum retry attempts
        backoff_strategies (Optional[List[BackoffStrategy]]): list of backoff strategies to use to determine how long
        to wait before retrying
    """

    DEFAULT_BACKOFF_STRATEGY = ExponentialBackoffStrategy

    parameters: InitVar[Mapping[str, Any]]
    config: Config
    response_filters: Optional[List[HttpResponseFilter]] = None
    max_retries: Optional[int] = 5
    max_time: int = 60 * 10
    _max_retries: int = field(init=False, repr=False, default=5)
    _max_time: int = field(init=False, repr=False, default=60 * 10)
    backoff_strategies: Optional[List[BackoffStrategy]] = None

    def __post_init__(self, parameters: Mapping[str, Any]) -> None:
        self.response_filters = self.response_filters or []

        self.response_filters.append(HttpResponseFilter(config=self.config, parameters={}))

        if not self.backoff_strategies:
            self.backoff_strategies = [DefaultErrorHandler.DEFAULT_BACKOFF_STRATEGY(parameters=parameters, config=self.config)]

        self._last_request_to_attempt_count: MutableMapping[requests.PreparedRequest, int] = {}

    def interpret_response(self, response_or_exception: Optional[Union[requests.Response, Exception]]) -> ErrorResolution:

        if isinstance(response_or_exception, requests.Response):
            if response_or_exception.ok:
                return SUCCESS_RESOLUTION

            request = response_or_exception.request

            if request not in self._last_request_to_attempt_count:
                self._last_request_to_attempt_count = {request: 1}
            else:
                self._last_request_to_attempt_count[request] += 1
            if self.response_filters:
                for response_filter in self.response_filters:
                    matched_status = response_filter.matches(response=response_or_exception)
                    if matched_status is not None:
                        return matched_status
        # Return default error resolution (retry)
        return DEFAULT_ERROR_RESOLUTION

    def backoff_time(
        self, response_or_exception: Optional[Union[requests.Response, requests.RequestException]], attempt_count: int
    ) -> Optional[float]:
        backoff = None
        if self.backoff_strategies:
            for backoff_strategies in self.backoff_strategies:
                backoff = backoff_strategies.backoff_time(response_or_exception, attempt_count)
                if backoff:
                    return backoff
        return backoff
