"""ForgeOne Resource Controller.

Host-protection layer for local inference and other memory-heavy workloads.
Born from the FORGE-003 Metal out-of-memory incident.

Public API::

    from services.resource_controller import (
        ResourceController, ResourcePolicy, AdmissionRequest, Outcome,
        ModelMetadata, MacOSTelemetrySource, MockTokenCounter,
        ProcessSupervisor, SubprocessAdapter, WatchdogThresholds,
    )

Design rule: the controller either admits a request exactly as specified, or
rejects it with a named :class:`Outcome`. It never silently reduces context,
substitutes a model, or reports a rejection as a success.
"""

from __future__ import annotations

from .admission import AdmissionController, AdmissionDecision, AdmissionRequest
from .controller import CooldownActive, ResourceController, SupervisedStart
from .estimator import Estimate, MetadataUnavailable, ModelMetadata, estimate
from .outcomes import Outcome, is_rejection, is_success
from .policy import PolicyError, ResourcePolicy
from .ports import (
    PortProbe,
    PortStatus,
    PortUnavailable,
    RealPortProbe,
    StaticPortProbe,
    require_free_port,
)
from .protected import (
    ProtectedResult,
    ProtectedServer,
    ProtectedStartup,
    RecordingForwarder,
    RequestForwarder,
)
from .gateway import (
    GatewayError,
    GatewayHTTPServer,
    ProtectedGateway,
    make_handler,
)
from .transport import (
    HttpRequestForwarder,
    NonLoopbackEndpoint,
    TransportError,
)
from .watchdog import (
    ProcessWatchdogReadiness,
    StaticWatchdogReadiness,
    WatchdogReadiness,
    count_telemetry_samples,
    launch_watchdog_process,
)
from .reservation import ConcurrencyExceeded, Reservation, ReservationManager
from .server_config import (
    APPROVED_EXECUTABLE_BASENAMES,
    REQUIRED_SERVER_FLAGS,
    UNBOUNDED_PATH_FLAGS,
    CacheBudget,
    ProtectedServerConfig,
    ServerConfigError,
    UnsupportedServingPath,
    UnsupportedServerVersion,
    serving_path_is_bounded,
    supported_flags_from_help,
    verify_server_support,
)
from .supervisor import (
    BindPolicyError,
    DuplicateServiceError,
    FakeProcessAdapter,
    OwnershipMismatchError,
    ProcessIdentity,
    ProcessSupervisor,
    SubprocessAdapter,
    SupervisorError,
    fingerprint_cmdline,
)
from .telemetry import (
    MacOSTelemetrySource,
    MemorySnapshot,
    SyntheticTelemetrySource,
    TelemetrySource,
    TelemetryUnavailable,
    UnavailableTelemetrySource,
    make_snapshot,
)
from .tokenization import (
    HuggingFaceTokenCounter,
    MockTokenCounter,
    TokenCounter,
    TokenizerUnavailable,
)
from .watchdog import (
    JsonlTelemetryWriter,
    MemoryTelemetryWriter,
    TelemetryWriter,
    Watchdog,
    WatchdogThresholds,
    WatchdogVerdict,
    evaluate,
)

__all__ = [
    "APPROVED_EXECUTABLE_BASENAMES",
    "AdmissionController",
    "AdmissionDecision",
    "AdmissionRequest",
    "BindPolicyError",
    "CacheBudget",
    "ConcurrencyExceeded",
    "CooldownActive",
    "DuplicateServiceError",
    "Estimate",
    "GatewayError",
    "GatewayHTTPServer",
    "HttpRequestForwarder",
    "NonLoopbackEndpoint",
    "ProtectedGateway",
    "make_handler",
    "PortProbe",
    "PortStatus",
    "PortUnavailable",
    "ProcessWatchdogReadiness",
    "TransportError",
    "count_telemetry_samples",
    "launch_watchdog_process",
    "ProtectedResult",
    "ProtectedServer",
    "ProtectedServerConfig",
    "ProtectedStartup",
    "REQUIRED_SERVER_FLAGS",
    "RealPortProbe",
    "RecordingForwarder",
    "RequestForwarder",
    "Reservation",
    "ReservationManager",
    "ServerConfigError",
    "StaticPortProbe",
    "StaticWatchdogReadiness",
    "UNBOUNDED_PATH_FLAGS",
    "UnsupportedServingPath",
    "UnsupportedServerVersion",
    "WatchdogReadiness",
    "require_free_port",
    "serving_path_is_bounded",
    "supported_flags_from_help",
    "verify_server_support",
    "FakeProcessAdapter",
    "HuggingFaceTokenCounter",
    "JsonlTelemetryWriter",
    "MacOSTelemetrySource",
    "MemorySnapshot",
    "MemoryTelemetryWriter",
    "MetadataUnavailable",
    "MockTokenCounter",
    "ModelMetadata",
    "Outcome",
    "OwnershipMismatchError",
    "PolicyError",
    "ProcessIdentity",
    "ProcessSupervisor",
    "ResourceController",
    "ResourcePolicy",
    "SubprocessAdapter",
    "SupervisedStart",
    "SupervisorError",
    "SyntheticTelemetrySource",
    "TelemetrySource",
    "TelemetryUnavailable",
    "TelemetryWriter",
    "TokenCounter",
    "TokenizerUnavailable",
    "UnavailableTelemetrySource",
    "Watchdog",
    "WatchdogThresholds",
    "WatchdogVerdict",
    "estimate",
    "evaluate",
    "fingerprint_cmdline",
    "is_rejection",
    "is_success",
    "make_snapshot",
]
