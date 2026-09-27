/* People's faces: the round avatar, and the photo picker that makes one (v0.25.0, ADR #402).
 *
 * `Avatar` is a person's photo, or — with none — their initials on a colour derived from their
 * username, so the same person is the same colour everywhere and a default costs no bytes at all.
 * A photo is `/api/avatars/<id>?v=<digest>`: the digest makes the URL immutable, so each browser
 * fetches each photo once, however many screens show it.
 *
 * `PhotoPicker` never sends the file the operator chose. It decodes it here, crops the centre
 * square, draws it at 192 px on a canvas and sends the canvas's re-encoding (WebP, or PNG where
 * the browser cannot write WebP). That strips EXIF — location included — colour profiles and
 * anything appended to the file, and it is why the server's 64 KiB and 320 px limits are never
 * met by an honest photo. The preview is the canvas itself, so the page's `img-src 'self'` policy
 * needs no `blob:` or `data:` exception.
 */

import { html, Component, cx } from "./dom.js";
import { api } from "./api.js";

const SIDE = 192;
const MAX_INPUT = 20 * 1024 * 1024;
const MAX_UPLOAD = 64 * 1024;
const ACCEPT = "image/png,image/jpeg,image/webp";

/** Two initials: from a display name's first two words, else from the username. */
export function initials(name, username) {
  const words = String(name || "").trim().split(/\s+/).filter(Boolean);
  if (words.length >= 2) return (words[0][0] + words[1][0]).toUpperCase();
  const base = words[0] || String(username || "?");
  return base.slice(0, 2).toUpperCase();
}

/** One of eight palette slots, stable per username. */
function hue(key) {
  let h = 0;
  for (const ch of String(key || "")) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return h % 8;
}

/** The URL of a person's photo, or null. */
export function photoUrl(id, digest) {
  return id != null && digest ? `/api/avatars/${id}?v=${digest}` : null;
}

export class Avatar extends Component {
  constructor(props) {
    super(props);
    this.state = { failed: null };
  }

  render({ id, digest, name, username, size = 32, className }, { failed }) {
    const url = photoUrl(id, digest);
    const label = name || username || "";
    const style = `width:${size}px;height:${size}px;font-size:${Math.round(size * 0.4)}px`;
    if (url && failed !== url) {
      return html`<img class=${cx("avatar", className)} src=${url} width=${size} height=${size}
        alt="" loading="lazy" decoding="async" title=${label} style=${style}
        onError=${() => this.setState({ failed: url })} />`;
    }
    // The initials are drawn by CSS from an attribute, not as text: a face is decoration beside a
    // name that is already there, and text here would read into every sentence it sits in.
    return html`<span class=${cx("avatar", "avatar-initials", `avatar-c${hue(username || label)}`,
        className)} style=${style} title=${label} aria-hidden="true"
        data-initials=${initials(name, username)}></span>`;
  }
}

function blobOf(canvas, type, quality) {
  return new Promise((resolve) => canvas.toBlob(resolve, type, quality));
}

/** The chosen file, re-encoded as a centred 192 px square. Throws a sentence on refusal. */
export async function encodePhoto(file, canvas) {
  if (!file) throw new Error("No file was chosen.");
  if (!ACCEPT.split(",").includes(file.type)) throw new Error("Choose a PNG, JPEG or WebP image.");
  if (file.size > MAX_INPUT) throw new Error("That file is larger than 20 MB.");
  const bitmap = await globalThis.createImageBitmap(file);
  try {
    const side = Math.min(bitmap.width, bitmap.height);
    canvas.width = SIDE;
    canvas.height = SIDE;
    const ctx = canvas.getContext("2d");
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(bitmap, (bitmap.width - side) / 2, (bitmap.height - side) / 2, side, side,
      0, 0, SIDE, SIDE);
  } finally {
    bitmap.close();
  }
  for (const quality of [0.85, 0.7, 0.55]) {
    const webp = await blobOf(canvas, "image/webp", quality);
    if (webp && webp.type === "image/webp" && webp.size <= MAX_UPLOAD) return webp;
  }
  const png = await blobOf(canvas, "image/png");
  if (png && png.size <= MAX_UPLOAD) return png;
  throw new Error("The photo could not be made small enough; try a simpler image.");
}

/** Send a re-encoded photo as the request body. */
export function uploadPhoto(path, blob) {
  return api(path, { method: "POST", body: blob, headers: { "Content-Type": blob.type } });
}

/**
 * A round preview and two buttons. `onChange(blob)` on a new photo, `onChange(null)` on removal.
 * `person` is `{ id, digest, name, username }` — what to show before anything is chosen.
 */
export class PhotoPicker extends Component {
  constructor(props) {
    super(props);
    this.state = { chosen: false, removed: false, error: null, busy: false };
    this.canvas = null;
  }

  async pick(event) {
    const file = event.currentTarget.files && event.currentTarget.files[0];
    event.currentTarget.value = "";
    if (!file) return;
    this.setState({ busy: true, error: null });
    try {
      const blob = await encodePhoto(file, this.canvas);
      this.setState({ chosen: true, removed: false, busy: false });
      this.props.onChange(blob);
    } catch (error) {
      this.setState({ busy: false, error: error.message || "That image could not be read." });
    }
  }

  remove() {
    this.setState({ chosen: false, removed: true, error: null });
    this.props.onChange(null);
  }

  render({ person = {}, size = 96, label = "Photo" }, { chosen, removed, error, busy }) {
    const shown = removed ? { ...person, digest: null } : person;
    const has = chosen || (!removed && person.digest);
    const style = `width:${size}px;height:${size}px`;
    return html`<div class="photo-picker">
      <div class="photo-frame" style=${style}>
        <canvas ref=${(node) => { this.canvas = node; }} class=${cx("avatar", !chosen && "photo-idle")}
          width=${SIDE} height=${SIDE} style=${style} aria-label=${`${label}: new photo`}></canvas>
        ${chosen ? null : html`<${Avatar} id=${shown.id} digest=${shown.digest} name=${shown.name}
          username=${shown.username} size=${size} />`}
      </div>
      <div class="photo-actions">
        <label class=${cx("tap", "photo-choose", busy && "busy")}>
          <input type="file" accept=${ACCEPT} class="visually-hidden"
            onChange=${(e) => this.pick(e)} disabled=${busy} />
          ${has ? "Change photo" : "Add photo"}</label>
        ${has ? html`<button type="button" class="linkish" onClick=${() => this.remove()}>Remove</button>` : null}
        ${error ? html`<p class="err" role="alert">${error}</p>` : null}
      </div>
    </div>`;
  }
}
