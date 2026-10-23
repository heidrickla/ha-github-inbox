"""Counts of open issues, open pull requests and items with unseen activity, and the last delivery."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, override

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from . import model
from .coordinator import InboxConfigEntry, InboxCoordinator
from .entity import InboxEntity

# Push only: nothing here polls.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class InboxSensorDescription(SensorEntityDescription):
    value: Callable[[dict[str, Any]], StateType | dt.datetime]


SENSORS: tuple[InboxSensorDescription, ...] = (
    InboxSensorDescription(
        key="open_issues",
        translation_key="open_issues",
        state_class=SensorStateClass.MEASUREMENT,
        value=lambda d: d["issues"],
    ),
    InboxSensorDescription(
        key="open_pull_requests",
        translation_key="open_pull_requests",
        state_class=SensorStateClass.MEASUREMENT,
        value=lambda d: d["prs"],
    ),
    InboxSensorDescription(
        key="unseen",
        translation_key="unseen",
        state_class=SensorStateClass.MEASUREMENT,
        value=lambda d: d["unseen"],
    ),
    InboxSensorDescription(
        key="last_delivery",
        translation_key="last_delivery",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value=lambda d: model.parse(d["last_delivery"]),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: InboxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(InboxSensor(coordinator, d) for d in SENSORS)


class InboxSensor(InboxEntity, SensorEntity):
    entity_description: InboxSensorDescription

    def __init__(
        self, coordinator: InboxCoordinator, description: InboxSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    @override
    def native_value(self) -> StateType | dt.datetime:
        return self.entity_description.value(self.coordinator.data)
