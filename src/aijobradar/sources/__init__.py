from aijobradar.config import AppConfig
from aijobradar.sources.base import Adapter
from aijobradar.sources.himalayas import HimalayasAdapter
from aijobradar.sources.jobicy import JobicyAdapter
from aijobradar.sources.wwr import WwrAdapter


def build_adapters(cfg: AppConfig) -> list[Adapter]:
    adapters: list[Adapter] = []
    if cfg.himalayas.enabled:
        adapters.append(
            HimalayasAdapter(
                max_pages=cfg.himalayas.max_pages,
                lookback_hours=cfg.himalayas.lookback_hours,
                page_delay_s=cfg.himalayas.page_delay_s,
                parent_categories=tuple(cfg.himalayas.parent_categories),
            )
        )
    if cfg.jobicy.enabled:
        adapters.append(
            JobicyAdapter(count=cfg.jobicy.count, industries=tuple(cfg.jobicy.industries))
        )
    if cfg.wwr.enabled:
        adapters.append(WwrAdapter(feeds=tuple(cfg.wwr.feeds)))
    return adapters
