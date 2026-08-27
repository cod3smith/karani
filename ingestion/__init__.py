from .models import (
    Job, PreFilterResult, RemoteStatus, RoleCategory, Seniority, Source,
)
from .base import Fetcher
from .ashby import AshbyFetcher
from .greenhouse import GreenhouseFetcher
from .himalayas import HimalayasFetcher
from .lever import LeverFetcher
from .remoteok import RemoteOKFetcher
from .weworkremotely import WeWorkRemotelyFetcher
from .remotive import RemotiveFetcher
from .workable import WorkableFetcher
from .aijobs import AIJobsFetcher

FETCHERS: dict[Source, Fetcher] = {
    Source.GREENHOUSE: GreenhouseFetcher(),
    Source.LEVER: LeverFetcher(),
    Source.ASHBY: AshbyFetcher(),
    Source.REMOTEOK: RemoteOKFetcher(),
    Source.HIMALAYAS: HimalayasFetcher(),
    Source.WEWORKREMOTELY: WeWorkRemotelyFetcher(),
    Source.REMOTIVE: RemotiveFetcher(),
    Source.WORKABLE: WorkableFetcher(),
    Source.AIJOBS: AIJobsFetcher(),
}

__all__ = [
    "FETCHERS", "Fetcher", "Job", "PreFilterResult",
    "RemoteStatus", "RoleCategory", "Seniority", "Source",
]
