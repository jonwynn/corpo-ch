/* Refreshes one server-rendered match without replacing the surrounding controls. */
(function (root) {
    "use strict";

    class MatchRefresh {
        constructor(options) {
            this.options = options;
            this.match_id = options.match_id;
            this.digest = options.digest;
            this.state = options.state;
            this.now = options.now || (() => Date.now());
            this.set_timer = options.set_timer || ((callback, delay) => setTimeout(callback, delay));
            this.clear_timer = options.clear_timer || ((timer) => clearTimeout(timer));
            this.last_success = this.now();
            this.failures = 0;
            this.generation = 0;
            this.in_flight = false;
            this.paused = false;
            this.stopped = false;
            this.timer = null;
            this.age_timer = null;
            this.cancel = null;
        }

        start() {
            if (this.state === "setup_changed") {
                this.stop();
                this.options.setup_changed();
                return;
            }
            this.describe();
            this.watch_age();
            this.schedule(this.interval());
        }

        interval() {
            return this.state === "complete" ? 10000 : 2000;
        }

        describe() {
            if (this.stopped) return;
            const seconds = Math.max(0, Math.floor((this.now() - this.last_success) / 1000));
            const stale = seconds >= (this.state === "complete" ? 20 : 10);
            let message = `Recorded match state · checked ${seconds}s ago`;
            if (this.paused) message = `Updates paused while hidden · checked ${seconds}s ago`;
            else if (this.failures) message = `Refresh failed · showing last recorded state from ${seconds}s ago · retrying`;
            else if (stale) message = `Updates delayed · showing last recorded state from ${seconds}s ago`;
            this.options.notify(message, this.failures > 0 || stale);
        }

        watch_age() {
            if (this.stopped || this.paused) return;
            this.age_timer = this.set_timer(() => {
                this.describe();
                this.watch_age();
            }, 1000);
        }

        schedule(delay) {
            this.clear_timer(this.timer);
            if (!this.stopped && !this.paused) {
                this.timer = this.set_timer(() => this.refresh(), delay);
            }
        }

        async refresh() {
            if (this.stopped || this.paused || this.in_flight) return;
            this.clear_timer(this.timer);
            this.in_flight = true;
            const generation = this.generation;
            const controller = new AbortController();
            let timeout;
            const cancellation = new Promise((resolve, reject) => {
                this.cancel = () => {
                    controller.abort();
                    reject(new Error("Request cancelled"));
                };
                timeout = this.set_timer(this.cancel, 8000);
            });
            try {
                const result = await Promise.race([this.options.request(controller.signal), cancellation]);
                if (generation !== this.generation || this.stopped || this.paused) return;
                if ([401, 403, 404].includes(result.status)) {
                    this.stop();
                    this.options.clear(result.status);
                    return;
                }
                if (result.status !== 200 || result.match_id !== this.match_id || result.version !== "1.0.0" || !result.digest) {
                    throw new Error("Invalid match response");
                }
                if (result.digest !== this.digest) this.options.apply(result);
                this.digest = result.digest;
                this.state = result.state;
                this.last_success = this.now();
                this.failures = 0;
                if (this.state === "setup_changed") {
                    this.stop();
                    this.options.setup_changed();
                    return;
                }
                if (result.polling_enabled === false) {
                    this.stop();
                    this.options.disabled();
                    return;
                }
                this.describe();
            } catch (error) {
                if (generation === this.generation && !this.stopped && !this.paused) {
                    this.failures += 1;
                    this.describe();
                }
            } finally {
                this.clear_timer(timeout);
                this.cancel = null;
                this.in_flight = false;
                const delays = [2000, 4000, 8000, 10000];
                this.schedule(generation !== this.generation ? 0 : this.failures ? delays[Math.min(this.failures - 1, 3)] : this.interval());
            }
        }

        set_hidden(hidden) {
            if (this.stopped || this.paused === hidden) return;
            this.paused = hidden;
            this.generation += 1;
            this.clear_timer(this.timer);
            this.clear_timer(this.age_timer);
            if (this.cancel) this.cancel();
            this.describe();
            if (!hidden) {
                this.watch_age();
                this.schedule(0);
            }
        }

        stop() {
            this.stopped = true;
            this.generation += 1;
            this.clear_timer(this.timer);
            this.clear_timer(this.age_timer);
            if (this.cancel) this.cancel();
        }
    }

    function initialize_viewer(document) {
        const theme_control = document.getElementById("match-viewer-theme");
        const themes = ["dark", "light", "system"];
        let saved_theme;
        try { saved_theme = root.localStorage.getItem("corpo-match-viewer-theme"); } catch (error) { /* Storage may be disabled. */ }
        if (themes.includes(saved_theme)) document.body.dataset.theme = saved_theme;
        if (theme_control) {
            if (themes.includes(saved_theme)) {
                theme_control.value = saved_theme;
            }
            theme_control.addEventListener("change", () => {
                const theme = themes.includes(theme_control.value) ? theme_control.value : "dark";
                document.body.dataset.theme = theme;
                try { root.localStorage.setItem("corpo-match-viewer-theme", theme); } catch (error) { /* The current selection still applies. */ }
            });
        }
        const state = document.getElementById("match-viewer-state");
        const status = document.getElementById("match-viewer-connection");
        if (!state || !state.dataset.stateUrl || !status) return;
        const retry = document.getElementById("match-viewer-retry");
        const reload = document.getElementById("match-viewer-reload");
        const assignment = document.getElementById("match-viewer-assignment");
        const announcement = document.getElementById("match-viewer-announcement");
        const announce = (message) => { if (announcement) announcement.textContent = message; };
        let last_warning = false;
        const url = new URL(state.dataset.stateUrl, root.location.href);
        if (url.origin !== root.location.origin) return;
        if (assignment) url.searchParams.set("pins", assignment.textContent);
        const controller = new MatchRefresh({
            match_id: state.dataset.matchId,
            digest: state.dataset.digest,
            state: state.dataset.state,
            request: async (signal) => {
                const response = await root.fetch(url, {signal, cache: "no-store", credentials: "same-origin", redirect: "error", headers: {Accept: "text/html"}});
                if (response.status !== 200) return {status: response.status};
                if (!(response.headers.get("Content-Type") || "").includes("text/html")) throw new Error("Unexpected content type");
                const html = await response.text();
                const parsed = new DOMParser().parseFromString(html, "text/html");
                const elements = parsed.querySelectorAll("#match-viewer-state");
                if (elements.length !== 1 || parsed.querySelector("script")) throw new Error("Invalid match fragment");
                const element = elements[0];
                return {status: 200, match_id: element.dataset.matchId, version: element.dataset.contractVersion, digest: element.dataset.digest, state: element.dataset.state, polling_enabled: element.dataset.pollingEnabled === "true", element};
            },
            apply: (result) => {
                const old_state = document.getElementById("match-viewer-state");
                const old_details = document.getElementById("match-viewer-details");
                const new_details = result.element.querySelector("#match-viewer-details");
                if (old_details && new_details) new_details.open = old_details.open;
                const focus = old_state.contains(document.activeElement) ? document.activeElement : null;
                const details_focused = focus && old_details && focus === old_details.querySelector("summary");
                const scroll_x = root.scrollX;
                const scroll_y = root.scrollY;
                old_state.replaceWith(result.element);
                if (details_focused && new_details) new_details.querySelector("summary").focus({preventScroll: true});
                root.scrollTo(scroll_x, scroll_y);
                const score = [...result.element.querySelectorAll(".mv-score-player")].map((player) => `${player.querySelector(".mv-slot-label")?.textContent} ${player.querySelector(".mv-points")?.textContent}`).join(", ");
                const current = result.element.querySelector(".mv-current h2");
                announce(["Match updated.", score, current?.textContent].filter(Boolean).join(" ").replace(/\s+/g, " "));
            },
            notify: (message, failed) => {
                if (status.textContent !== message) status.textContent = message;
                retry.hidden = !failed;
                if (failed !== last_warning) announce(failed ? "Updates delayed. The last recorded match state remains visible." : "Live updates restored.");
                last_warning = failed;
            },
            clear: (code) => {
                const region = document.getElementById("match-viewer-state");
                region.replaceChildren();
                const message = document.createElement("p");
                message.className = "mv-empty-state";
                message.textContent = code === 401 ? "Sign in again to view this match." : "This match is no longer available to your account.";
                region.append(message);
                status.textContent = "Updates stopped";
                retry.hidden = true;
                reload.textContent = "Reload viewer";
                reload.hidden = false;
                announce(message.textContent);
            },
            setup_changed: () => {
                status.textContent = "Player assignments changed · reload to confirm the new slots";
                retry.hidden = true;
                reload.hidden = false;
                announce(status.textContent);
            },
            disabled: () => {
                status.textContent = "Automatic updates are off · reload to refresh";
                retry.hidden = true;
                announce(status.textContent);
            },
        });
        retry.addEventListener("click", () => controller.refresh());
        document.addEventListener("visibilitychange", () => controller.set_hidden(document.hidden));
        root.addEventListener("pagehide", () => controller.stop());
        // A cached navigation must recheck access rather than revive a stopped controller.
        root.addEventListener("pageshow", (event) => { if (event.persisted) root.location.reload(); });
        controller.start();
        if (document.hidden) controller.set_hidden(true);
    }

    if (typeof module !== "undefined" && module.exports) module.exports = {MatchRefresh, initialize_viewer};
    else if (root.document) initialize_viewer(root.document);
})(typeof window !== "undefined" ? window : globalThis);
