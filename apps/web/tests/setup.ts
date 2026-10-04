import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// `globals` is disabled, so React Testing Library cannot auto-register cleanup.
afterEach(() => {
  cleanup();
});

// jsdom does not implement the modal-dialog methods. This models the part of the platform the
// shell relies on: showModal opens, close closes and fires `close`. (Focus trapping and inertness
// are browser behaviour and are covered by the manual review, not by this polyfill.)
if (typeof HTMLDialogElement !== "undefined" && typeof HTMLDialogElement.prototype.showModal !== "function") {
  HTMLDialogElement.prototype.showModal = function showModal(this: HTMLDialogElement) {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.show = HTMLDialogElement.prototype.showModal;
  HTMLDialogElement.prototype.close = function close(this: HTMLDialogElement) {
    if (this.hasAttribute("open")) {
      this.removeAttribute("open");
      this.dispatchEvent(new Event("close"));
    }
  };
}
