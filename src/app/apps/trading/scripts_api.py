"""Omnix Scripts over HTTP (TVP-11.1, 11.2): check a script, run it on a chart's bars, and the names for autocomplete.

Scripts are stored as trading documents (``/api/trading/scripts``, record type ``script``). A run reads the chart's
bars on the server and runs the script in a worker process (``scripts_service.py``): a script never reaches orders,
files or the network.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.security.tenant_context import current_tenant

from .repositories import RepositoryFactory, default_trading_repository
from .scripts_screener import SCREEN_MAX_INSTRUMENTS, ScriptScreenOutput, ScriptScreenRow, screen_script
from .scripts_service import BACKTEST_LIMITS, ScriptRunService, ScriptServiceError, bars_for_script, check_script, default_script_service
from .service import TradingMarketDataService, default_market_data_service

logger = logging.getLogger(__name__)

# A script's source, as the editor and the documents keep it.
MAX_SOURCE_LENGTH = 200_000

# Names the editor completes besides the functions, constants and series.
KEYWORDS = (
    "if", "else", "for", "to", "by", "in", "while", "switch", "var", "varip", "true", "false", "na", "and", "or", "not",
    "break", "continue", "import", "export", "method", "type", "int", "float", "bool", "string", "color", "series", "simple", "const",
)
# Parameters of functions the interpreter binds by hand, for the editor's hover (as Pine documents them).
_INPUT_COMMON = "tooltip, inline, group, confirm"
EXTRA_SIGNATURES = {
    "indicator": "title, shorttitle, overlay, format, precision, scale, max_bars_back, max_lines_count, max_labels_count, max_boxes_count",
    "strategy": "title, shorttitle, overlay, format, precision, pyramiding, initial_capital, default_qty_type, default_qty_value",
    "hline": "price, title, color, linestyle, linewidth, editable, display",
    "fill": "plot1, plot2, color, title, editable, show_last, fillgaps, display",
    "alert": "message, freq",
    "input": "defval, title, tooltip, inline, group",
    "input.int": f"defval, title, minval, maxval, step, {_INPUT_COMMON}",
    "input.float": f"defval, title, minval, maxval, step, {_INPUT_COMMON}",
    "input.bool": f"defval, title, {_INPUT_COMMON}",
    "input.string": f"defval, title, options, {_INPUT_COMMON}",
    "input.color": f"defval, title, {_INPUT_COMMON}",
    "input.source": "defval, title, tooltip, inline, group",
    "input.timeframe": f"defval, title, options, {_INPUT_COMMON}",
    "input.symbol": f"defval, title, {_INPUT_COMMON}",
    "input.session": f"defval, title, options, {_INPUT_COMMON}",
    "input.price": f"defval, title, {_INPUT_COMMON}",
    "input.time": f"defval, title, {_INPUT_COMMON}",
    "input.text_area": f"defval, title, {_INPUT_COMMON}",
    "math.max": "number0, number1, ...",
    "math.min": "number0, number1, ...",
    "math.avg": "number0, number1, ...",
    "str.format": "formatString, arg0, arg1, ...",
    "array.from": "arg0, arg1, ...",
    "array.push": "id, value",
    "array.pop": "id",
    "array.get": "id, index",
    "array.set": "id, index, value",
    "array.size": "id",
    "array.clear": "id",
    "array.sum": "id",
    "array.avg": "id",
    "array.max": "id",
    "array.min": "id",
}
VARIABLES = (
    "bar_index", "last_bar_index", "barstate.isfirst", "barstate.islast", "barstate.isconfirmed", "barstate.isnew", "barstate.isrealtime",
    "barstate.ishistory", "syminfo.ticker", "syminfo.tickerid", "syminfo.mintick", "syminfo.timezone", "timeframe.period",
    "timeframe.multiplier", "timeframe.isintraday", "timeframe.isdaily", "timeframe.isweekly", "timeframe.ismonthly", "timenow",
)


class ScriptCheckRequest(BaseModel):
    source: str = Field(max_length=MAX_SOURCE_LENGTH)


class ScriptDiagnostic(BaseModel):
    kind: str
    message: str
    line: int
    column: int


class ScriptCheckResponse(BaseModel):
    diagnostics: list[ScriptDiagnostic]
    declaration: dict[str, Any] | None = None
    inputs: list[dict[str, Any]] = Field(default_factory=list)


class ScriptRunRequest(BaseModel):
    source: str = Field(max_length=MAX_SOURCE_LENGTH)
    instrument_id: str = Field(min_length=3, max_length=200)
    binding_id: str | None = Field(default=None, max_length=240)
    interval: str = Field(min_length=1, max_length=16)
    inputs: dict[str, Any] = Field(default_factory=dict)
    limit: int = Field(default=1_000, ge=10, le=5_000)
    # Time per top-level statement, for the editor's profiler (TVP-11.2).
    profile: bool = False


class ScriptBacktestRequest(BaseModel):
    source: str = Field(max_length=MAX_SOURCE_LENGTH)
    instrument_id: str = Field(min_length=3, max_length=200)
    binding_id: str | None = Field(default=None, max_length=240)
    interval: str = Field(min_length=1, max_length=16)
    inputs: dict[str, Any] = Field(default_factory=dict)
    # Deep backtesting: up to this many of the latest bars the provider has (TVP-11.5).
    bars: int = Field(default=20_000, ge=10, le=20_000)


class ScriptRunResponse(BaseModel):
    # The start of each bar the script ran on (ISO), one per value in each plot.
    times: list[str]
    result: dict[str, Any] | None = None
    # The script's problem (with its line) or the service's (busy, stopped); the result is null then.
    error: ScriptDiagnostic | None = None


class ScriptScreenRequest(BaseModel):
    source: str = Field(max_length=MAX_SOURCE_LENGTH)
    instrument_ids: list[str] = Field(min_length=1, max_length=SCREEN_MAX_INSTRUMENTS)
    interval: str = Field(default="1d", min_length=1, max_length=16)
    inputs: dict[str, Any] = Field(default_factory=dict)
    limit: int = Field(default=500, ge=50, le=2_000)


class ScriptScreenResponse(BaseModel):
    outputs: list[ScriptScreenOutput]
    rows: list[ScriptScreenRow]
    # The script's own problem (it doesn't compile): nothing was screened.
    error: ScriptDiagnostic | None = None


class ScriptVersionSummary(BaseModel):
    revision: int
    name: str
    saved_at: str
    characters: int
    lines: int


class ScriptVersionListResponse(BaseModel):
    versions: list[ScriptVersionSummary]


class ScriptVersion(BaseModel):
    revision: int
    name: str
    saved_at: str
    source: str


class ScriptReferenceResponse(BaseModel):
    functions: list[str]
    constants: list[str]
    variables: list[str]
    keywords: list[str]
    # Each function's parameters (``"source, length"``), where the interpreter declares them.
    signatures: dict[str, str] = Field(default_factory=dict)


def create_trading_scripts_router(
    service_factory: Callable[[], ScriptRunService] = default_script_service,
    market_service_factory: Callable[[], TradingMarketDataService] = default_market_data_service,
    repository_factory: RepositoryFactory = default_trading_repository,
) -> APIRouter:
    router = APIRouter(prefix="/api/trading/scripts", tags=["trading-scripts"])

    @router.post("/check", response_model=ScriptCheckResponse)
    async def check(request: ScriptCheckRequest) -> ScriptCheckResponse:
        """Compile a script: its problems with their line and column, its declaration and inputs."""
        return ScriptCheckResponse.model_validate(await asyncio.to_thread(check_script, request.source))

    @router.post("/run", response_model=ScriptRunResponse)
    async def run(request: ScriptRunRequest) -> ScriptRunResponse:
        """Run a script on the latest ``limit`` bars of a chart (as the chart reads them, clock-aligned)."""
        from .scripts_security import load_script_securities

        try:
            response = await asyncio.to_thread(
                market_service_factory().bars, request.instrument_id, request.interval, request.limit, request.binding_id, alignment="clock",
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            logger.warning("script_bars_failed", exc_info=True)
            raise HTTPException(status_code=502, detail={"code": "market_data_failed", "message": "The market data provider request failed."}) from exc
        bars = list(response.bars)
        times = [bar.start_time.isoformat() for bar in bars]
        user_id = str(getattr(current_tenant(), "user_id", "") or "")
        # request.security(): the other symbols' and timeframes' bars (TVP-11.1).
        securities = await asyncio.to_thread(
            load_script_securities, request.source, request.instrument_id, request.interval, bars, market_service_factory(), request.inputs,
        )
        try:
            result = await asyncio.to_thread(
                service_factory().run, request.source, bars_for_script(bars), inputs=request.inputs,
                symbol=request.instrument_id, timeframe=request.interval, user_id=user_id, profile=request.profile, securities=securities,
            )
        except ScriptServiceError as error:
            return ScriptRunResponse(times=times, error=ScriptDiagnostic(**error.payload()))
        return ScriptRunResponse(times=times, result=result)

    @router.post("/screen", response_model=ScriptScreenResponse)
    async def screen(request: ScriptScreenRequest) -> ScriptScreenResponse:
        """Run a script on each listed symbol's latest ``limit`` bars (TVP-11.6): every output's last two values per symbol."""
        checked = await asyncio.to_thread(check_script, request.source)
        if checked["diagnostics"]:
            return ScriptScreenResponse(outputs=[], rows=[], error=ScriptDiagnostic(**checked["diagnostics"][0]))
        user_id = str(getattr(current_tenant(), "user_id", "") or "")
        outputs, rows = await asyncio.to_thread(
            screen_script, request.source, list(dict.fromkeys(request.instrument_ids)), interval=request.interval, limit=request.limit,
            inputs=request.inputs, user_id=user_id, script_service=service_factory(), market_service=market_service_factory(),
        )
        return ScriptScreenResponse(outputs=outputs, rows=rows)

    @router.post("/backtest", response_model=ScriptRunResponse)
    async def backtest(request: ScriptBacktestRequest) -> ScriptRunResponse:
        """A strategy script over all the history the provider serves (up to ``bars``), for the strategy tester.

        A research backtest: the script trades a simulated account on these bars and never reaches an order gateway.
        """
        from .scripts_security import load_script_securities

        try:
            response = await asyncio.to_thread(
                market_service_factory().bars, request.instrument_id, request.interval, request.bars, request.binding_id, alignment="clock",
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            logger.warning("script_backtest_bars_failed", exc_info=True)
            raise HTTPException(status_code=502, detail={"code": "market_data_failed", "message": "The market data provider request failed."}) from exc
        bars = list(response.bars)
        times = [bar.start_time.isoformat() for bar in bars]
        user_id = str(getattr(current_tenant(), "user_id", "") or "")
        securities = await asyncio.to_thread(
            load_script_securities, request.source, request.instrument_id, request.interval, bars, market_service_factory(), request.inputs,
        )
        try:
            result = await asyncio.to_thread(
                service_factory().run, request.source, bars_for_script(bars), inputs=request.inputs,
                symbol=request.instrument_id, timeframe=request.interval, user_id=user_id, limits=BACKTEST_LIMITS, securities=securities,
            )
        except ScriptServiceError as error:
            return ScriptRunResponse(times=times, error=ScriptDiagnostic(**error.payload()))
        if not result.get("strategy"):
            return ScriptRunResponse(times=times, error=ScriptDiagnostic(kind="strategy", message="only strategy() scripts can be backtested", line=0, column=0))
        # The tester reads the report; the plots and drawings stay with the chart's run.
        slim = {key: result[key] for key in ("declaration", "inputs", "strategy", "bars", "seconds", "logs")}
        return ScriptRunResponse(times=times, result=slim)

    @router.get("/reference", response_model=ScriptReferenceResponse)
    def reference() -> ScriptReferenceResponse:
        """The names the editor completes: functions, named constants, built-in series and variables, keywords."""
        from .scripts.builtins import COLORS, FUNCTIONS, _NAMED_CONSTANTS
        from .scripts.runtime import SERIES_NAMES

        return ScriptReferenceResponse(
            functions=sorted(FUNCTIONS),
            constants=sorted({*_NAMED_CONSTANTS, *(f"color.{name}" for name in COLORS)}),
            variables=sorted({*SERIES_NAMES, *VARIABLES}),
            keywords=list(KEYWORDS),
            signatures={
                **{name: spec for name, spec in EXTRA_SIGNATURES.items() if name in FUNCTIONS},
                **{name: str(signature) for name, factory in FUNCTIONS.items() if (signature := getattr(factory, "signature", None))},
            },
        )

    @router.get("/{record_id}/versions", response_model=ScriptVersionListResponse)
    def versions(record_id: str) -> ScriptVersionListResponse:
        """A script's saved versions, newest first (TVP-11.3); restoring one saves it again as a new version."""
        return ScriptVersionListResponse.model_validate({"versions": repository_factory().script_versions(record_id)})

    @router.get("/{record_id}/versions/{revision}", response_model=ScriptVersion)
    def version(record_id: str, revision: int) -> ScriptVersion:
        found = repository_factory().script_version(record_id, revision)
        if found is None:
            raise HTTPException(status_code=404, detail=f"script {record_id} has no version {revision}")
        return ScriptVersion.model_validate(found)

    return router
