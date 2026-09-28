"use strict";

const assert = require("node:assert/strict");
const {test} = require("node:test");
const {MatchRefresh} = require("../corpoch/static/corpoch/match_viewer.js");

function create_harness() {
    const timers = new Map();
    const applied = [];
    const messages = [];
    const cleared = [];
    let time = 0;
    let identifier = 0;
    let response = {status: 200, match_id: "match-a", version: "1.0.0", digest: "next", state: "awaiting_result"};
    const controller = new MatchRefresh({
        match_id: "match-a", digest: "initial", state: "opening_bans",
        now: () => time,
        set_timer: (callback, delay) => { timers.set(++identifier, {callback, delay}); return identifier; },
        clear_timer: (timer) => timers.delete(timer),
        request: () => typeof response === "function" ? response() : Promise.resolve(response),
        apply: (value) => applied.push(value),
        notify: (value, failed) => messages.push({value, failed}),
        clear: (value) => cleared.push(value),
        setup_changed: () => messages.push({value: "setup_changed"}),
        disabled: () => messages.push({value: "disabled"}),
    });
    return {controller, timers, applied, messages, cleared, set_time: (value) => {time = value;}, respond: (value) => {response = value;}};
}

test("one response replaces the whole state; unchanged digests only refresh freshness", async () => {
    const harness = create_harness();
    await harness.controller.refresh();
    harness.set_time(12000);
    await harness.controller.refresh();
    assert.equal(harness.applied.length, 1);
    assert.equal(harness.controller.last_success, 12000);
    assert.equal(harness.controller.failures, 0);
});

test("failed requests retain the last score and back off to ten seconds", async () => {
    const harness = create_harness();
    await harness.controller.refresh();
    harness.respond({status: 503});
    for (const delay of [2000, 4000, 8000, 10000, 10000]) {
        await harness.controller.refresh();
        assert.equal(harness.timers.get(harness.controller.timer).delay, delay);
    }
    assert.equal(harness.applied.length, 1);
    assert.equal(harness.controller.digest, "next");
    assert.match(harness.messages.at(-1).value, /showing last recorded state/);
});

test("access loss or removal clears private content and stops refresh", async () => {
    for (const status of [401, 403, 404]) {
        const harness = create_harness();
        harness.respond({status});
        await harness.controller.refresh();
        assert.deepEqual(harness.cleared, [status]);
        assert.equal(harness.controller.stopped, true);
        assert.equal(harness.timers.size, 0);
    }
});

test("wrong match, invalid protocol, and missing digest cannot replace valid data", async () => {
    for (const change of [{match_id: "match-b"}, {version: "2.0.0"}, {digest: ""}, {status: 302}]) {
        const harness = create_harness();
        harness.respond({status: 200, match_id: "match-a", version: "1.0.0", digest: "x", state: "complete", ...change});
        await harness.controller.refresh();
        assert.equal(harness.applied.length, 0);
        assert.equal(harness.controller.failures, 1);
    }
});

test("slow responses do not permit a second concurrent request", async () => {
    const harness = create_harness();
    let finish;
    let calls = 0;
    harness.respond(() => {calls++; return new Promise((resolve) => {finish = resolve;});});
    const pending = harness.controller.refresh();
    await harness.controller.refresh();
    assert.equal(calls, 1);
    finish({status: 503});
    await pending;
    assert.equal(harness.controller.in_flight, false);
});

test("timeout preserves data and ignores a late success", async () => {
    const harness = create_harness();
    let finish;
    harness.respond(() => new Promise((resolve) => {finish = resolve;}));
    const pending = harness.controller.refresh();
    [...harness.timers.values()].find((timer) => timer.delay === 8000).callback();
    await pending;
    finish({status: 200, match_id: "match-a", version: "1.0.0", digest: "late", state: "complete"});
    await Promise.resolve();
    assert.equal(harness.applied.length, 0);
    assert.equal(harness.controller.failures, 1);
});

test("leaving or changing matches prevents an old response from changing the page", async () => {
    const harness = create_harness();
    let finish;
    harness.respond(() => new Promise((resolve) => {finish = resolve;}));
    const pending = harness.controller.refresh();
    harness.controller.stop();
    finish({status: 200, match_id: "match-a", version: "1.0.0", digest: "late", state: "complete"});
    await pending;
    assert.equal(harness.applied.length, 0);
    assert.equal(harness.timers.size, 0);
});

test("hidden tabs pause and resume schedules a new read", async () => {
    const harness = create_harness();
    harness.controller.start();
    harness.controller.set_hidden(true);
    await harness.controller.refresh();
    assert.equal(harness.timers.size, 0);
    assert.equal(harness.applied.length, 0);
    harness.controller.set_hidden(false);
    assert.equal(harness.timers.get(harness.controller.timer).delay, 0);
});

test("completion slows polling but later corrections restore the active interval", async () => {
    const harness = create_harness();
    harness.respond({status: 200, match_id: "match-a", version: "1.0.0", digest: "complete", state: "complete"});
    await harness.controller.refresh();
    assert.equal(harness.timers.get(harness.controller.timer).delay, 10000);
    harness.respond({status: 200, match_id: "match-a", version: "1.0.0", digest: "undo", state: "awaiting_result"});
    await harness.controller.refresh();
    assert.equal(harness.timers.get(harness.controller.timer).delay, 2000);
    assert.equal(harness.applied.length, 2);
});

test("changed assignments stop at the explicit reload checkpoint", async () => {
    const harness = create_harness();
    harness.respond({status: 200, match_id: "match-a", version: "1.0.0", digest: "setup", state: "setup_changed"});
    await harness.controller.refresh();
    assert.equal(harness.controller.stopped, true);
    assert.equal(harness.messages.at(-1).value, "setup_changed");
});

test("staleness uses the time of the last success; reconnect removes the warning", async () => {
    const harness = create_harness();
    harness.set_time(11000);
    harness.controller.describe();
    assert.equal(harness.messages.at(-1).failed, true);
    await harness.controller.refresh();
    assert.equal(harness.messages.at(-1).failed, false);
    assert.equal(harness.controller.last_success, 11000);
});

test("operator disabling polling stops existing pages after their final authorized snapshot", async () => {
    const harness = create_harness();
    harness.respond({status: 200, match_id: "match-a", version: "1.0.0", digest: "final", state: "awaiting_result", polling_enabled: false});
    await harness.controller.refresh();
    assert.equal(harness.applied.length, 1);
    assert.equal(harness.controller.stopped, true);
    assert.equal(harness.messages.at(-1).value, "disabled");
    assert.equal(harness.timers.size, 0);
});
