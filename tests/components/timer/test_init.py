"""The tests for the timer component."""
# pylint: disable=protected-access
from datetime import timedelta
import logging
from unittest.mock import patch

import pytest

from homeassistant.components.timer import (
    ATTR_DURATION,
    ATTR_FINISHES_AT,
    ATTR_REMAINING,
    ATTR_RESTORE,
    CONF_DURATION,
    CONF_ICON,
    CONF_NAME,
    CONF_RESTORE,
    DEFAULT_DURATION,
    DOMAIN,
    EVENT_TIMER_CANCELLED,
    EVENT_TIMER_FINISHED,
    EVENT_TIMER_MODIFIED,
    EVENT_TIMER_PAUSED,
    EVENT_TIMER_RESTARTED,
    EVENT_TIMER_STARTED,
    SERVICE_CANCEL,
    SERVICE_FINISH,
    SERVICE_MODIFY,
    SERVICE_PAUSE,
    SERVICE_START,
    STATUS_ACTIVE,
    STATUS_IDLE,
    STATUS_PAUSED,
    Timer,
    _format_timedelta,
)
from homeassistant.const import (
    ATTR_EDITABLE,
    ATTR_FRIENDLY_NAME,
    ATTR_ICON,
    ATTR_ID,
    ATTR_NAME,
    CONF_ENTITY_ID,
    CONF_ID,
    EVENT_STATE_CHANGED,
    SERVICE_RELOAD,
)
from homeassistant.core import Context, CoreState, State
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.restore_state import (
    DATA_RESTORE_STATE_TASK,
    RestoreStateData,
    StoredState,
)
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow

from tests.common import async_capture_events, async_fire_time_changed

_LOGGER = logging.getLogger(__name__)


@pytest.fixture
def storage_setup(hass, hass_storage):
    """Storage setup."""

    async def _storage(items=None, config=None):
        if items is None:
            hass_storage[DOMAIN] = {
                "key": DOMAIN,
                "version": 1,
                "data": {
                    "items": [
                        {
                            ATTR_ID: "from_storage",
                            ATTR_NAME: "timer from storage",
                            ATTR_DURATION: "0:00:00",
                            ATTR_RESTORE: False,
                        }
                    ]
                },
            }
        else:
            hass_storage[DOMAIN] = {
                "key": DOMAIN,
                "version": 1,
                "data": {"items": items},
            }
        if config is None:
            config = {DOMAIN: {}}
        return await async_setup_component(hass, DOMAIN, config)

    return _storage


async def test_config(hass):
    """Test config."""
    invalid_configs = [None, 1, {}, {"name with space": None}]

    for cfg in invalid_configs:
        assert not await async_setup_component(hass, DOMAIN, {DOMAIN: cfg})


async def test_config_options(hass):
    """Test configuration options."""
    count_start = len(hass.states.async_entity_ids())

    _LOGGER.debug("ENTITIES @ start: %s", hass.states.async_entity_ids())

    config = {
        DOMAIN: {
            "test_1": {},
            "test_2": {
                CONF_NAME: "Hello World",
                CONF_ICON: "mdi:work",
                CONF_DURATION: 10,
            },
            "test_3": None,
        }
    }

    assert await async_setup_component(hass, "timer", config)
    await hass.async_block_till_done()

    assert count_start + 3 == len(hass.states.async_entity_ids())
    await hass.async_block_till_done()

    state_1 = hass.states.get("timer.test_1")
    state_2 = hass.states.get("timer.test_2")
    state_3 = hass.states.get("timer.test_3")

    assert state_1 is not None
    assert state_2 is not None
    assert state_3 is not None

    assert state_1.state == STATUS_IDLE
    assert ATTR_ICON not in state_1.attributes
    assert ATTR_FRIENDLY_NAME not in state_1.attributes

    assert state_2.state == STATUS_IDLE
    assert state_2.attributes.get(ATTR_FRIENDLY_NAME) == "Hello World"
    assert state_2.attributes.get(ATTR_ICON) == "mdi:work"
    assert state_2.attributes.get(ATTR_DURATION) == "0:00:10"

    assert state_3.state == STATUS_IDLE
    assert str(cv.time_period(DEFAULT_DURATION)) == state_3.attributes.get(
        CONF_DURATION
    )


async def test_methods_and_events(hass):
    """Test methods and events."""
    hass.state = CoreState.starting

    await async_setup_component(hass, DOMAIN, {DOMAIN: {"test1": {CONF_DURATION: 10}}})

    state = hass.states.get("timer.test1")
    assert state
    assert state.state == STATUS_IDLE

    results = []

    def fake_event_listener(event):
        """Fake event listener for trigger."""
        results.append(event)

    hass.bus.async_listen(EVENT_TIMER_STARTED, fake_event_listener)
    hass.bus.async_listen(EVENT_TIMER_RESTARTED, fake_event_listener)
    hass.bus.async_listen(EVENT_TIMER_PAUSED, fake_event_listener)
    hass.bus.async_listen(EVENT_TIMER_FINISHED, fake_event_listener)
    hass.bus.async_listen(EVENT_TIMER_CANCELLED, fake_event_listener)

    steps = [
        {"call": SERVICE_START, "state": STATUS_ACTIVE, "event": EVENT_TIMER_STARTED},
        {"call": SERVICE_PAUSE, "state": STATUS_PAUSED, "event": EVENT_TIMER_PAUSED},
        {"call": SERVICE_START, "state": STATUS_ACTIVE, "event": EVENT_TIMER_RESTARTED},
        {"call": SERVICE_CANCEL, "state": STATUS_IDLE, "event": EVENT_TIMER_CANCELLED},
        {"call": SERVICE_START, "state": STATUS_ACTIVE, "event": EVENT_TIMER_STARTED},
        {"call": SERVICE_FINISH, "state": STATUS_IDLE, "event": EVENT_TIMER_FINISHED},
        {"call": SERVICE_FINISH, "state": STATUS_IDLE, "event": None},
        {"call": SERVICE_START, "state": STATUS_ACTIVE, "event": EVENT_TIMER_STARTED},
        {"call": SERVICE_PAUSE, "state": STATUS_PAUSED, "event": EVENT_TIMER_PAUSED},
        {"call": SERVICE_CANCEL, "state": STATUS_IDLE, "event": EVENT_TIMER_CANCELLED},
        {"call": SERVICE_START, "state": STATUS_ACTIVE, "event": EVENT_TIMER_STARTED},
        {"call": SERVICE_START, "state": STATUS_ACTIVE, "event": EVENT_TIMER_RESTARTED},
        {"call": SERVICE_PAUSE, "state": STATUS_PAUSED, "event": EVENT_TIMER_PAUSED},
        {"call": SERVICE_FINISH, "state": STATUS_IDLE, "event": EVENT_TIMER_FINISHED},
    ]

    expectedEvents = 0
    for step in steps:
        if step["call"] is not None:
            await hass.services.async_call(
                DOMAIN, step["call"], {CONF_ENTITY_ID: "timer.test1"}
            )
            await hass.async_block_till_done()

        state = hass.states.get("timer.test1")
        assert state
        if step["state"] is not None:
            assert state.state == step["state"], str(step)

        if step["event"] is not None:
            expectedEvents += 1
            assert results[-1].event_type == step["event"]
            assert len(results) == expectedEvents


async def test_start_service(hass):
    """Test the start/stop service."""
    await async_setup_component(hass, DOMAIN, {DOMAIN: {"test1": {CONF_DURATION: 10}}})

    state = hass.states.get("timer.test1")
    assert state
    assert state.state == STATUS_IDLE
    assert state.attributes[ATTR_DURATION] == "0:00:10"

    await hass.services.async_call(
        DOMAIN, SERVICE_START, {CONF_ENTITY_ID: "timer.test1"}
    )
    await hass.async_block_till_done()
    state = hass.states.get("timer.test1")
    assert state
    assert state.state == STATUS_ACTIVE
    assert state.attributes[ATTR_DURATION] == "0:00:10"
    assert state.attributes[ATTR_REMAINING] == "0:00:10"

    await hass.services.async_call(
        DOMAIN, SERVICE_CANCEL, {CONF_ENTITY_ID: "timer.test1"}
    )
    await hass.async_block_till_done()
    state = hass.states.get("timer.test1")
    assert state
    assert state.state == STATUS_IDLE
    assert state.attributes[ATTR_DURATION] == "0:00:10"
    assert ATTR_REMAINING not in state.attributes

    await hass.services.async_call(
        DOMAIN, SERVICE_START, {CONF_ENTITY_ID: "timer.test1", CONF_DURATION: 15}
    )
    await hass.async_block_till_done()
    state = hass.states.get("timer.test1")
    assert state
    assert state.state == STATUS_ACTIVE
    assert state.attributes[ATTR_DURATION] == "0:00:15"
    assert state.attributes[ATTR_REMAINING] == "0:00:15"


async def test_wait_till_timer_expires(hass):
    """Test for a timer to end."""
    hass.state = CoreState.starting

    await async_setup_component(hass, DOMAIN, {DOMAIN: {"test1": {CONF_DURATION: 10}}})

    state = hass.states.get("timer.test1")
    assert state
    assert state.state == STATUS_IDLE

    results = []

    def fake_event_listener(event):
        """Fake event listener for trigger."""
        results.append(event)

    hass.bus.async_listen(EVENT_TIMER_STARTED, fake_event_listener)
    hass.bus.async_listen(EVENT_TIMER_PAUSED, fake_event_listener)
    hass.bus.async_listen(EVENT_TIMER_FINISHED, fake_event_listener)
    hass.bus.async_listen(EVENT_TIMER_CANCELLED, fake_event_listener)

    await hass.services.async_call(
        DOMAIN, SERVICE_START, {CONF_ENTITY_ID: "timer.test1"}
    )
    await hass.async_block_till_done()

    state = hass.states.get("timer.test1")
    assert state
    assert state.state == STATUS_ACTIVE

    assert results[-1].event_type == EVENT_TIMER_STARTED
    assert len(results) == 1

    async_fire_time_changed(hass, utcnow() + timedelta(seconds=10))
    await hass.async_block_till_done()

    state = hass.states.get("timer.test1")
    assert state
    assert state.state == STATUS_IDLE

    assert results[-1].event_type == EVENT_TIMER_FINISHED
    assert len(results) == 2


async def test_no_initial_state_and_no_restore_state(hass):
    """Ensure that entity is create without initial and restore feature."""
    hass.state = CoreState.starting

    await async_setup_component(hass, DOMAIN, {DOMAIN: {"test1": {CONF_DURATION: 10}}})

    state = hass.states.get("timer.test1")
    assert state
    assert state.state == STATUS_IDLE


async def test_config_reload(hass, hass_admin_user, hass_read_only_user):
    """Test reload service."""
    count_start = len(hass.states.async_entity_ids())
    ent_reg = er.async_get(hass)

    _LOGGER.debug("ENTITIES @ start: %s", hass.states.async_entity_ids())

    config = {
        DOMAIN: {
            "test_1": {},
            "test_2": {
                CONF_NAME: "Hello World",
                CONF_ICON: "mdi:work",
                CONF_DURATION: 10,
            },
        }
    }

    assert await async_setup_component(hass, "timer", config)
    await hass.async_block_till_done()

    assert count_start + 2 == len(hass.states.async_entity_ids())
    await hass.async_block_till_done()

    state_1 = hass.states.get("timer.test_1")
    state_2 = hass.states.get("timer.test_2")
    state_3 = hass.states.get("timer.test_3")

    assert state_1 is not None
    assert state_2 is not None
    assert state_3 is None
    assert ent_reg.async_get_entity_id(DOMAIN, DOMAIN, "test_1") is not None
    assert ent_reg.async_get_entity_id(DOMAIN, DOMAIN, "test_2") is not None
    assert ent_reg.async_get_entity_id(DOMAIN, DOMAIN, "test_3") is None

    assert state_1.state == STATUS_IDLE
    assert ATTR_ICON not in state_1.attributes
    assert ATTR_FRIENDLY_NAME not in state_1.attributes

    assert state_2.state == STATUS_IDLE
    assert state_2.attributes.get(ATTR_FRIENDLY_NAME) == "Hello World"
    assert state_2.attributes.get(ATTR_ICON) == "mdi:work"
    assert state_2.attributes.get(ATTR_DURATION) == "0:00:10"

    with patch(
        "homeassistant.config.load_yaml_config_file",
        autospec=True,
        return_value={
            DOMAIN: {
                "test_2": {
                    CONF_NAME: "Hello World reloaded",
                    CONF_ICON: "mdi:work-reloaded",
                    CONF_DURATION: 20,
                },
                "test_3": {},
            }
        },
    ):
        with pytest.raises(Unauthorized):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_RELOAD,
                blocking=True,
                context=Context(user_id=hass_read_only_user.id),
            )
        await hass.services.async_call(
            DOMAIN,
            SERVICE_RELOAD,
            blocking=True,
            context=Context(user_id=hass_admin_user.id),
        )
        await hass.async_block_till_done()

    assert count_start + 2 == len(hass.states.async_entity_ids())

    state_1 = hass.states.get("timer.test_1")
    state_2 = hass.states.get("timer.test_2")
    state_3 = hass.states.get("timer.test_3")

    assert state_1 is None
    assert state_2 is not None
    assert state_3 is not None
    assert ent_reg.async_get_entity_id(DOMAIN, DOMAIN, "test_1") is None
    assert ent_reg.async_get_entity_id(DOMAIN, DOMAIN, "test_2") is not None
    assert ent_reg.async_get_entity_id(DOMAIN, DOMAIN, "test_3") is not None

    assert state_2.state == STATUS_IDLE
    assert state_2.attributes.get(ATTR_FRIENDLY_NAME) == "Hello World reloaded"
    assert state_2.attributes.get(ATTR_ICON) == "mdi:work-reloaded"
    assert state_2.attributes.get(ATTR_DURATION) == "0:00:20"

    assert state_3.state == STATUS_IDLE
    assert ATTR_ICON not in state_3.attributes
    assert ATTR_FRIENDLY_NAME not in state_3.attributes


async def test_timer_restarted_event(hass):
    """Ensure restarted event is called after starting a paused or running timer."""
    hass.state = CoreState.starting

    await async_setup_component(hass, DOMAIN, {DOMAIN: {"test1": {CONF_DURATION: 10}}})

    state = hass.states.get("timer.test1")
    assert state
    assert state.state == STATUS_IDLE

    results = []

    def fake_event_listener(event):
        """Fake event listener for trigger."""
        results.append(event)

    hass.bus.async_listen(EVENT_TIMER_STARTED, fake_event_listener)
    hass.bus.async_listen(EVENT_TIMER_RESTARTED, fake_event_listener)
    hass.bus.async_listen(EVENT_TIMER_PAUSED, fake_event_listener)
    hass.bus.async_listen(EVENT_TIMER_FINISHED, fake_event_listener)
    hass.bus.async_listen(EVENT_TIMER_CANCELLED, fake_event_listener)

    await hass.services.async_call(
        DOMAIN, SERVICE_START, {CONF_ENTITY_ID: "timer.test1"}
    )
    await hass.async_block_till_done()
    state = hass.states.get("timer.test1")
    assert state
    assert state.state == STATUS_ACTIVE

    assert results[-1].event_type == EVENT_TIMER_STARTED
    assert len(results) == 1

    await hass.services.async_call(
        DOMAIN, SERVICE_START, {CONF_ENTITY_ID: "timer.test1"}
    )
    await hass.async_block_till_done()
    state = hass.states.get("timer.test1")
    assert state
    assert state.state == STATUS_ACTIVE

    assert results[-1].event_type == EVENT_TIMER_RESTARTED
    assert len(results) == 2

    await hass.services.async_call(
        DOMAIN, SERVICE_PAUSE, {CONF_ENTITY_ID: "timer.test1"}
    )
    await hass.async_block_till_done()
    state = hass.states.get("timer.test1")
    assert state
    assert state.state == STATUS_PAUSED

    assert results[-1].event_type == EVENT_TIMER_PAUSED
    assert len(results) == 3

    await hass.services.async_call(
        DOMAIN, SERVICE_START, {CONF_ENTITY_ID: "timer.test1"}
    )
    await hass.async_block_till_done()
    state = hass.states.get("timer.test1")
    assert state
    assert state.state == STATUS_ACTIVE

    assert results[-1].event_type == EVENT_TIMER_RESTARTED
    assert len(results) == 4


async def test_state_changed_when_timer_restarted(hass):
    """Ensure timer's state changes when it restarted."""
    hass.state = CoreState.starting

    await async_setup_component(hass, DOMAIN, {DOMAIN: {"test1": {CONF_DURATION: 10}}})

    state = hass.states.get("timer.test1")
    assert state
    assert state.state == STATUS_IDLE

    results = []

    def fake_event_listener(event):
        """Fake event listener for trigger."""
        results.append(event)

    hass.bus.async_listen(EVENT_STATE_CHANGED, fake_event_listener)

    await hass.services.async_call(
        DOMAIN, SERVICE_START, {CONF_ENTITY_ID: "timer.test1"}
    )
    await hass.async_block_till_done()
    state = hass.states.get("timer.test1")
    assert state
    assert state.state == STATUS_ACTIVE

    assert results[-1].event_type == EVENT_STATE_CHANGED
    assert len(results) == 1

    await hass.services.async_call(
        DOMAIN, SERVICE_START, {CONF_ENTITY_ID: "timer.test1"}
    )
    await hass.async_block_till_done()
    state = hass.states.get("timer.test1")
    assert state
    assert state.state == STATUS_ACTIVE

    assert results[-1].event_type == EVENT_STATE_CHANGED
    assert len(results) == 2


async def test_load_from_storage(hass, storage_setup):
    """Test set up from storage."""
    assert await storage_setup()
    state = hass.states.get(f"{DOMAIN}.timer_from_storage")
    assert state.state == STATUS_IDLE
    assert state.attributes.get(ATTR_FRIENDLY_NAME) == "timer from storage"
    assert state.attributes.get(ATTR_EDITABLE)


async def test_editable_state_attribute(hass, storage_setup):
    """Test editable attribute."""
    assert await storage_setup(config={DOMAIN: {"from_yaml": None}})

    state = hass.states.get(f"{DOMAIN}.{DOMAIN}_from_storage")
    assert state.state == STATUS_IDLE
    assert state.attributes.get(ATTR_FRIENDLY_NAME) == "timer from storage"
    assert state.attributes.get(ATTR_EDITABLE)

    state = hass.states.get(f"{DOMAIN}.from_yaml")
    assert not state.attributes.get(ATTR_EDITABLE)
    assert state.state == STATUS_IDLE


async def test_ws_list(hass, hass_ws_client, storage_setup):
    """Test listing via WS."""
    assert await storage_setup(config={DOMAIN: {"from_yaml": None}})

    client = await hass_ws_client(hass)

    await client.send_json({"id": 6, "type": f"{DOMAIN}/list"})
    resp = await client.receive_json()
    assert resp["success"]

    storage_ent = "from_storage"
    yaml_ent = "from_yaml"
    result = {item["id"]: item for item in resp["result"]}

    assert len(result) == 1
    assert storage_ent in result
    assert yaml_ent not in result
    assert result[storage_ent][ATTR_NAME] == "timer from storage"


async def test_ws_delete(hass, hass_ws_client, storage_setup):
    """Test WS delete cleans up entity registry."""
    assert await storage_setup()

    timer_id = "from_storage"
    timer_entity_id = f"{DOMAIN}.{DOMAIN}_{timer_id}"
    ent_reg = er.async_get(hass)

    state = hass.states.get(timer_entity_id)
    assert state is not None
    from_reg = ent_reg.async_get_entity_id(DOMAIN, DOMAIN, timer_id)
    assert from_reg == timer_entity_id

    client = await hass_ws_client(hass)

    await client.send_json(
        {"id": 6, "type": f"{DOMAIN}/delete", f"{DOMAIN}_id": f"{timer_id}"}
    )
    resp = await client.receive_json()
    assert resp["success"]

    state = hass.states.get(timer_entity_id)
    assert state is None
    assert ent_reg.async_get_entity_id(DOMAIN, DOMAIN, timer_id) is None


async def test_update(hass, hass_ws_client, storage_setup):
    """Test updating timer entity."""

    assert await storage_setup()

    timer_id = "from_storage"
    timer_entity_id = f"{DOMAIN}.{DOMAIN}_{timer_id}"
    ent_reg = er.async_get(hass)

    state = hass.states.get(timer_entity_id)
    assert state.attributes[ATTR_FRIENDLY_NAME] == "timer from storage"
    assert ent_reg.async_get_entity_id(DOMAIN, DOMAIN, timer_id) == timer_entity_id

    client = await hass_ws_client(hass)

    await client.send_json(
        {
            "id": 6,
            "type": f"{DOMAIN}/update",
            f"{DOMAIN}_id": f"{timer_id}",
            CONF_DURATION: 33,
            CONF_RESTORE: True,
        }
    )
    resp = await client.receive_json()
    assert resp["success"]

    state = hass.states.get(timer_entity_id)
    assert state.attributes[ATTR_DURATION] == _format_timedelta(cv.time_period(33))
    assert state.attributes[ATTR_RESTORE]


async def test_ws_create(hass, hass_ws_client, storage_setup):
    """Test create WS."""
    assert await storage_setup(items=[])

    timer_id = "new_timer"
    timer_entity_id = f"{DOMAIN}.{timer_id}"
    ent_reg = er.async_get(hass)

    state = hass.states.get(timer_entity_id)
    assert state is None
    assert ent_reg.async_get_entity_id(DOMAIN, DOMAIN, timer_id) is None

    client = await hass_ws_client(hass)

    await client.send_json(
        {
            "id": 6,
            "type": f"{DOMAIN}/create",
            CONF_NAME: "New Timer",
            CONF_DURATION: 42,
        }
    )
    resp = await client.receive_json()
    assert resp["success"]

    state = hass.states.get(timer_entity_id)
    assert state.state == STATUS_IDLE
    assert state.attributes[ATTR_DURATION] == _format_timedelta(cv.time_period(42))
    assert ent_reg.async_get_entity_id(DOMAIN, DOMAIN, timer_id) == timer_entity_id


async def test_setup_no_config(hass, hass_admin_user):
    """Test component setup with no config."""
    count_start = len(hass.states.async_entity_ids())
    assert await async_setup_component(hass, DOMAIN, {})

    with patch(
        "homeassistant.config.load_yaml_config_file", autospec=True, return_value={}
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_RELOAD,
            blocking=True,
            context=Context(user_id=hass_admin_user.id),
        )
        await hass.async_block_till_done()

    assert count_start == len(hass.states.async_entity_ids())


async def test_restore_idle(hass):
    """Test entity restore logic when timer is idle."""
    utc_now = utcnow()
    stored_state = StoredState(
        State(
            "timer.test",
            STATUS_IDLE,
            {ATTR_DURATION: "0:00:30"},
        ),
        None,
        utc_now,
    )

    data = await RestoreStateData.async_get_instance(hass)
    await hass.async_block_till_done()
    await data.store.async_save([stored_state.as_dict()])

    # Emulate a fresh load
    hass.data.pop(DATA_RESTORE_STATE_TASK)

    entity = Timer(
        {
            CONF_ID: "test",
            CONF_NAME: "test",
            CONF_DURATION: "0:01:00",
            CONF_RESTORE: True,
        }
    )
    entity.hass = hass
    entity.entity_id = "timer.test"

    await entity.async_added_to_hass()
    await hass.async_block_till_done()
    assert entity.state == STATUS_IDLE
    assert entity.extra_state_attributes[ATTR_DURATION] == "0:00:30"
    assert ATTR_REMAINING not in entity.extra_state_attributes
    assert ATTR_FINISHES_AT not in entity.extra_state_attributes
    assert entity.extra_state_attributes[ATTR_RESTORE]


async def test_restore_paused(hass):
    """Test entity restore logic when timer is paused."""
    utc_now = utcnow()
    stored_state = StoredState(
        State(
            "timer.test",
            STATUS_PAUSED,
            {ATTR_DURATION: "0:00:30", ATTR_REMAINING: "0:00:15"},
        ),
        None,
        utc_now,
    )

    data = await RestoreStateData.async_get_instance(hass)
    await hass.async_block_till_done()
    await data.store.async_save([stored_state.as_dict()])

    # Emulate a fresh load
    hass.data.pop(DATA_RESTORE_STATE_TASK)

    entity = Timer(
        {
            CONF_ID: "test",
            CONF_NAME: "test",
            CONF_DURATION: "0:01:00",
            CONF_RESTORE: True,
        }
    )
    entity.hass = hass
    entity.entity_id = "timer.test"

    await entity.async_added_to_hass()
    await hass.async_block_till_done()
    assert entity.state == STATUS_PAUSED
    assert entity.extra_state_attributes[ATTR_DURATION] == "0:00:30"
    assert entity.extra_state_attributes[ATTR_REMAINING] == "0:00:15"
    assert ATTR_FINISHES_AT not in entity.extra_state_attributes
    assert entity.extra_state_attributes[ATTR_RESTORE]


async def test_restore_active_resume(hass):
    """Test entity restore logic when timer is active and end time is after startup."""
    events = async_capture_events(hass, EVENT_TIMER_RESTARTED)
    assert not events
    utc_now = utcnow()
    finish = utc_now + timedelta(seconds=30)
    simulated_utc_now = utc_now + timedelta(seconds=15)
    stored_state = StoredState(
        State(
            "timer.test",
            STATUS_ACTIVE,
            {ATTR_DURATION: "0:00:30", ATTR_FINISHES_AT: finish.isoformat()},
        ),
        None,
        utc_now,
    )

    data = await RestoreStateData.async_get_instance(hass)
    await hass.async_block_till_done()
    await data.store.async_save([stored_state.as_dict()])

    # Emulate a fresh load
    hass.data.pop(DATA_RESTORE_STATE_TASK)

    entity = Timer(
        {
            CONF_ID: "test",
            CONF_NAME: "test",
            CONF_DURATION: "0:01:00",
            CONF_RESTORE: True,
        }
    )
    entity.hass = hass
    entity.entity_id = "timer.test"

    # In patch make sure we ignore microseconds
    with patch(
        "homeassistant.components.timer.dt_util.utcnow",
        return_value=simulated_utc_now.replace(microsecond=999),
    ):
        await entity.async_added_to_hass()
        await hass.async_block_till_done()

    assert entity.state == STATUS_ACTIVE
    assert entity.extra_state_attributes[ATTR_DURATION] == "0:00:30"
    assert entity.extra_state_attributes[ATTR_REMAINING] == "0:00:15"
    assert entity.extra_state_attributes[ATTR_FINISHES_AT] == finish.isoformat()
    assert entity.extra_state_attributes[ATTR_RESTORE]
    assert len(events) == 1


async def test_restore_active_finished_outside_grace(hass):
    """Test entity restore logic: timer is active, ended while Home Assistant was stopped."""
    events = async_capture_events(hass, EVENT_TIMER_FINISHED)
    assert not events
    utc_now = utcnow()
    finish = utc_now + timedelta(seconds=30)
    simulated_utc_now = utc_now + timedelta(seconds=46)
    stored_state = StoredState(
        State(
            "timer.test",
            STATUS_ACTIVE,
            {ATTR_DURATION: "0:00:30", ATTR_FINISHES_AT: finish.isoformat()},
        ),
        None,
        utc_now,
    )

    data = await RestoreStateData.async_get_instance(hass)
    await hass.async_block_till_done()
    await data.store.async_save([stored_state.as_dict()])

    # Emulate a fresh load
    hass.data.pop(DATA_RESTORE_STATE_TASK)

    entity = Timer(
        {
            CONF_ID: "test",
            CONF_NAME: "test",
            CONF_DURATION: "0:01:00",
            CONF_RESTORE: True,
        }
    )
    entity.hass = hass
    entity.entity_id = "timer.test"

    with patch(
        "homeassistant.components.timer.dt_util.utcnow", return_value=simulated_utc_now
    ):
        await entity.async_added_to_hass()
        await hass.async_block_till_done()

    assert entity.state == STATUS_IDLE
    assert entity.extra_state_attributes[ATTR_DURATION] == "0:00:30"
    assert ATTR_REMAINING not in entity.extra_state_attributes
    assert ATTR_FINISHES_AT not in entity.extra_state_attributes
    assert entity.extra_state_attributes[ATTR_RESTORE]
    assert len(events) == 1


async def test_modify_service(hass):
    """Test the modify service."""
    results = []

    def fake_event_listener(event):
        """Fake event listener for trigger."""
        results.append(event)

    hass.bus.async_listen(EVENT_TIMER_STARTED, fake_event_listener)
    hass.bus.async_listen(EVENT_TIMER_PAUSED, fake_event_listener)
    hass.bus.async_listen(EVENT_TIMER_FINISHED, fake_event_listener)
    hass.bus.async_listen(EVENT_TIMER_CANCELLED, fake_event_listener)
    hass.bus.async_listen(EVENT_TIMER_MODIFIED, fake_event_listener)

    def _check_events(*events):
        events = list(events)
        while len(results) > 0 and len(events) > 0:
            expected_type, expected_duration = events.pop(0)
            event = results.pop(0)
            assert event.event_type == expected_type, results
            if expected_duration:
                assert event.data[ATTR_DURATION] == expected_duration
        assert len(events) == 0
        assert len(results) == 0

    async def _call_modify(duration):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_MODIFY,
            {CONF_ENTITY_ID: "timer.test1", ATTR_DURATION: duration},
        )
        await hass.async_block_till_done()

    def _check_state(state, duration, remaining):
        timer = hass.states.get("timer.test1")
        assert timer
        assert timer.state == state
        assert timer.attributes[ATTR_DURATION] == duration
        if remaining:
            assert timer.attributes[ATTR_REMAINING] == remaining
        else:
            assert ATTR_REMAINING not in timer.attributes

    await async_setup_component(hass, DOMAIN, {DOMAIN: {"test1": {CONF_DURATION: 10}}})

    # Test modifying running timers
    await hass.services.async_call(
        DOMAIN, SERVICE_START, {CONF_ENTITY_ID: "timer.test1"}
    )
    await hass.async_block_till_done()
    _check_events((EVENT_TIMER_STARTED, None))
    _check_state(STATUS_ACTIVE, "0:00:10", "0:00:10")

    await _call_modify("00:00:05")
    _check_events((EVENT_TIMER_MODIFIED, "0:00:05"))
    _check_state(STATUS_ACTIVE, "0:00:10", "0:00:15")

    await _call_modify("-00:00:10")
    _check_events((EVENT_TIMER_MODIFIED, "-0:00:10"))
    _check_state(STATUS_ACTIVE, "0:00:10", "0:00:05")

    await _call_modify("-00:00:10")
    _check_events((EVENT_TIMER_MODIFIED, "-0:00:10"), (EVENT_TIMER_FINISHED, None))
    _check_state(STATUS_IDLE, "0:00:10", None)

    # Test modifying idle timers
    await _call_modify("00:00:20")
    _check_events((EVENT_TIMER_MODIFIED, "0:00:20"))
    _check_state(STATUS_IDLE, "0:00:30", None)

    await _call_modify("-01:00:00")
    _check_events((EVENT_TIMER_MODIFIED, "-1:00:00"))
    _check_state(STATUS_IDLE, "0:00:00", None)

    # Test modifying paused timers
    await hass.services.async_call(
        DOMAIN,
        SERVICE_START,
        {CONF_ENTITY_ID: "timer.test1", ATTR_DURATION: "00:00:10"},
    )
    await hass.async_block_till_done()
    await hass.services.async_call(
        DOMAIN, SERVICE_PAUSE, {CONF_ENTITY_ID: "timer.test1"}
    )
    await hass.async_block_till_done()
    _check_state(STATUS_PAUSED, "0:00:10", "0:00:10")

    await _call_modify("00:00:05")
    _check_events(
        (EVENT_TIMER_STARTED, None),
        (EVENT_TIMER_PAUSED, None),
        (EVENT_TIMER_MODIFIED, "0:00:05"),
    )
    _check_state(STATUS_PAUSED, "0:00:10", "0:00:15")

    await _call_modify("-00:00:10")
    _check_events((EVENT_TIMER_MODIFIED, "-0:00:10"))
    _check_state(STATUS_PAUSED, "0:00:10", "0:00:05")

    await _call_modify("-00:00:10")
    _check_events((EVENT_TIMER_FINISHED, None), (EVENT_TIMER_MODIFIED, "-0:00:10"))
    _check_state(STATUS_IDLE, "0:00:10", None)

    assert len(results) == 0


def test_format_timedelta():
    """Test `timer._format_timedelta`."""
    assert _format_timedelta(timedelta(seconds=10000)) == "2:46:40"
    assert _format_timedelta(timedelta(seconds=1000)) == "0:16:40"
    assert _format_timedelta(timedelta(seconds=10)) == "0:00:10"
    assert _format_timedelta(timedelta(seconds=-10)) == "-0:00:10"
    assert _format_timedelta(timedelta(seconds=-1000)) == "-0:16:40"
    assert _format_timedelta(timedelta(seconds=-10000)) == "-2:46:40"
