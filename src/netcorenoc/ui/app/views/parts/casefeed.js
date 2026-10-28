/* A case's feed: the conversation and the history, newest first (ADR #461).
 *
 * Each post says who can read it — Internal (the NOC), Specified (the people it names), All (the
 * customer too) — so nobody writes for the NOC and is read by a customer. The case's own history
 * (taken, updated, set pending, back in the queue) is in the same stream, drawn quieter. What a
 * reader is shown is the server's choice; the composer offers what the server says this reader
 * may post (`can_post`).
 */

import { html, Component, cx } from "../../dom.js";
import { get, post } from "../../api.js";
import { can } from "../../session.js";
import { relative, timeTitle } from "../../format.js";
import { Icon } from "../../icons.js";
import { roster } from "../../roster.js";
import { AgentGlyph } from "./nocui.js";

const SHORT = { internal: "Internal", specified: "Specified", all: "All with access" };

/** Who wrote it, as a mark: an agent's glyph, the appliance's, or a person's initial. */
function Author({ author }) {
  if (author && author.kind === "agent") {
    const known = (roster() || []).find((a) => a.name === author.name);
    return html`<${AgentGlyph} agent=${known || { role: "engineer", icon: "bot" }} size="sm" />`;
  }
  if (author && author.kind === "system") {
    return html`<span class="feed-avatar feed-avatar-system"><${Icon} name="pulse" /></span>`;
  }
  return html`<span class="feed-avatar" aria-hidden="true">${((author && author.name) || "?").slice(0, 1).toUpperCase()}</span>`;
}

export class CaseFeed extends Component {
  constructor(props) {
    super(props);
    this.state = { feed: null, error: null, body: "", visibility: "internal", readers: [],
      people: null, busy: false, note: null, events: true };
  }

  componentDidMount() { this.read(); }

  componentDidUpdate(previous) {
    if (previous.version !== this.props.version) this.read();
  }

  async read() {
    try {
      const feed = await get(`/api/noc/cases/${this.props.id}/feed`);
      const visibility = feed.can_post.includes(this.state.visibility) ? this.state.visibility
        : feed.can_post[0];
      this.setState({ feed, error: null, visibility });
    } catch (error) {
      this.setState({ error: error.message });
    }
  }

  async choose(visibility) {
    this.setState({ visibility });
    if (visibility === "specified" && this.state.people === null) {
      try { this.setState({ people: (await get("/api/noc/people")).people }); } catch { /* listed below */ }
    }
  }

  async submit(event) {
    event.preventDefault();
    const st = this.state;
    this.setState({ busy: true, note: null });
    try {
      const sent = await post(`/api/noc/cases/${this.props.id}/feed`, {
        body: st.body, visibility: st.visibility,
        readers: st.visibility === "specified" ? st.readers : [],
      });
      this.setState({ busy: false, body: "", readers: [], note: sent.requeued
        ? "Posted — the case is back in the NOC queue for someone to read it." : null });
      this.read();
      if (sent.requeued) this.props.onRequeued();
    } catch (error) {
      this.setState({ busy: false, note: error.message });
    }
  }

  composer(feed) {
    const st = this.state;
    const id = `feed-${this.props.id}`;
    return html`<form class="feed-compose" onSubmit=${(e) => this.submit(e)}>
      <label for=${`${id}-body`} class="visually-hidden">Post</label>
      <textarea id=${`${id}-body`} rows="3" maxlength="8000" placeholder="Share an update…"
        value=${st.body} onInput=${(e) => this.setState({ body: e.currentTarget.value })}></textarea>
      <div class="feed-compose-row">
        <div class="feed-vis" role="radiogroup" aria-label="Who can read it">
          ${feed.can_post.map((v) => html`<label key=${v} class=${cx("feed-vis-opt", st.visibility === v && "on")}>
            <input type="radio" name=${`${id}-vis`} value=${v} checked=${st.visibility === v}
              onChange=${() => this.choose(v)} /> ${SHORT[v]}</label>`)}
        </div>
        <button type="submit" class="primary" disabled=${st.busy || !st.body.trim()
          || (st.visibility === "specified" && !st.readers.length)}>${st.busy ? "Posting…" : "Post"}</button>
      </div>
      ${st.visibility === "specified" ? html`<div class="feed-readers">
        ${st.people === null ? html`<span class="muted">Reading who you can name…</span>`
          : st.people.map((p) => html`<label key=${p.id} class="check-row">
            <input type="checkbox" checked=${st.readers.includes(p.id)}
              onChange=${(e) => this.setState({ readers: e.currentTarget.checked
                ? [...st.readers, p.id] : st.readers.filter((r) => r !== p.id) })} /> ${p.username}</label>`)}
      </div>` : null}
      ${st.visibility === "all" ? html`<p class="hint">Visible to everyone who can read this case,
        the customer included.</p>` : null}
      ${st.note ? html`<p class="hint" role="status">${st.note}</p>` : null}
    </form>`;
  }

  render(_props, st) {
    if (st.error) return html`<p class="err" role="alert">${st.error}</p>`;
    if (!st.feed) return html`<p class="muted">Reading the feed…</p>`;
    const posts = st.feed.posts.filter((p) => st.events || p.kind === "post");
    return html`<section class="case-feed" aria-label="Feed">
      ${can("cases.post") ? this.composer(st.feed) : null}
      <label class="check-row feed-events"><input type="checkbox" checked=${st.events}
        onChange=${(e) => this.setState({ events: e.currentTarget.checked })} /> Show the case's history</label>
      <ol class="feed-list">${posts.map((p) => html`<li key=${p.id}
          class=${cx("feed-item", p.kind === "event" ? "feed-event" : `feed-${p.visibility}`)}>
        <${Author} author=${p.author} />
        <div class="feed-main">
          <div class="feed-head">
            <b>${p.author ? p.author.name : "—"}</b>
            ${p.kind === "post" ? html`<span class=${cx("feed-badge", `feed-badge-${p.visibility}`)}>
              ${SHORT[p.visibility]}</span>` : null}
            <span class="muted" title=${timeTitle(p.created_at)}>${relative(p.created_at)}</span>
          </div>
          <p class="feed-body">${p.body}</p>
        </div>
      </li>`)}</ol>
      ${st.feed.more ? html`<p class="muted">Older posts are not shown.</p>` : null}
    </section>`;
  }
}
