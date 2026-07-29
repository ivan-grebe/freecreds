import { el } from "./dom.js";

export function createCombo({
  input,
  list,
  matches,
  renderItem,
  displayText,
  exactShortcuts,
  onSelect,
}) {
  const state = { items: [], filtered: [], activeIndex: -1, selected: null };

  function setOpen(open) {
    list.classList.toggle("open", open);
    input.setAttribute("aria-expanded", open ? "true" : "false");
  }

  function render() {
    list.replaceChildren();
    if (!state.filtered.length) {
      list.appendChild(el("li", { class: "combo-empty" }, "No matches"));
      return;
    }
    if (state.filtered.length > 5) {
      list.appendChild(el(
        "li",
        { class: "combo-count" },
        `${state.filtered.length} matches: scroll for more`,
      ));
    }
    state.filtered.forEach((item, index) => {
      const option = el("li", {
        class: `combo-item${index === state.activeIndex ? " active" : ""}`,
        role: "option",
        "data-index": String(index),
      });
      for (const part of [].concat(renderItem(item))) {
        if (part instanceof Node) option.appendChild(part);
        else if (part != null) option.appendChild(document.createTextNode(String(part)));
      }
      option.addEventListener("mousedown", (event) => {
        // This runs before input blur closes the list.
        event.preventDefault();
        select(index);
      });
      list.appendChild(option);
    });
    if (state.activeIndex >= 0) {
      list.querySelector(`[data-index="${state.activeIndex}"]`)
        ?.scrollIntoView({ block: "nearest" });
    }
  }

  function updateFiltered() {
    const query = input.value.trim().toLowerCase();
    const tokens = query ? query.split(/\s+/) : [];
    state.filtered = state.items.filter((item) => matches(item, tokens));
    if (!state.filtered.length) state.activeIndex = -1;
    else if (state.activeIndex >= state.filtered.length || state.activeIndex < 0) {
      state.activeIndex = 0;
    }
    render();
  }

  function select(index) {
    const item = state.filtered[index];
    if (!item) return;
    state.selected = item;
    input.value = displayText(item);
    setOpen(false);
    onSelect?.(item);
  }

  function openOnFocus() {
    if (input.disabled) return;
    updateFiltered();
    setOpen(true);
  }

  input.addEventListener("input", () => {
    state.selected = null;
    updateFiltered();
    setOpen(true);
  });
  input.addEventListener("focus", openOnFocus);
  input.addEventListener("click", openOnFocus);
  document.addEventListener("mousedown", (event) => {
    if (!input.contains(event.target) && !list.contains(event.target)) setOpen(false);
  });
  input.addEventListener("keydown", (event) => {
    if (!list.classList.contains("open")) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        openOnFocus();
        event.preventDefault();
      }
      return;
    }
    if (event.key === "ArrowDown") {
      if (state.filtered.length) {
        state.activeIndex = (state.activeIndex + 1) % state.filtered.length;
        render();
      }
      event.preventDefault();
    } else if (event.key === "ArrowUp") {
      if (state.filtered.length) {
        state.activeIndex = (
          state.activeIndex - 1 + state.filtered.length
        ) % state.filtered.length;
        render();
      }
      event.preventDefault();
    } else if (event.key === "Enter") {
      if (state.activeIndex >= 0) {
        select(state.activeIndex);
        event.preventDefault();
      }
    } else if (event.key === "Escape") {
      setOpen(false);
      event.preventDefault();
    }
  });

  return {
    setItems(items) {
      state.items = items;
      state.filtered = items.slice();
      state.activeIndex = -1;
      state.selected = null;
      input.value = "";
    },
    getSelected() {
      return state.selected;
    },
    setEnabled(enabled, placeholder) {
      input.disabled = !enabled;
      if (placeholder !== undefined) input.placeholder = placeholder;
    },
    reset() {
      state.items = [];
      state.filtered = [];
      state.activeIndex = -1;
      state.selected = null;
      input.value = "";
      setOpen(false);
    },
    tryPromoteTypedSelection() {
      if (state.selected) return state.selected;
      const typed = input.value.trim().toLowerCase();
      if (!typed) return null;
      const shortcuts = exactShortcuts || (() => []);
      const match = state.items.find((item) => (
        displayText(item).toLowerCase() === typed
        || (shortcuts(item) || []).some((value) => value.toLowerCase() === typed)
      ));
      if (match) state.selected = match;
      return match || null;
    },
  };
}
