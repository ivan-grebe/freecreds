import { el } from "./dom.js";

export function createCombo({
  input,
  list,
  matches,
  renderItem,
  displayText,
  exactShortcuts,
  onSelect,
  onInput,
}) {
  const state = { items: [], filtered: [], activeIndex: -1, selected: null };

  function syncActiveOption() {
    if (list.classList.contains("open") && state.activeIndex >= 0) {
      input.setAttribute("aria-activedescendant", `${list.id}-option-${state.activeIndex}`);
    } else {
      input.removeAttribute("aria-activedescendant");
    }
  }

  function setOpen(open) {
    list.classList.toggle("open", open);
    input.setAttribute("aria-expanded", open ? "true" : "false");
    syncActiveOption();
  }

  function render() {
    list.replaceChildren();
    syncActiveOption();
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
        id: `${list.id}-option-${index}`,
        "aria-selected": String(index === state.activeIndex),
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
    onInput?.();
    updateFiltered();
    setOpen(true);
  });
  input.addEventListener("focus", openOnFocus);
  input.addEventListener("click", openOnFocus);
  input.addEventListener("blur", () => {
    setOpen(false);
    if (!state.selected) promoteTypedSelection();
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

  function promoteTypedSelection() {
    if (state.selected) return state.selected;
    const typed = input.value.trim().toLowerCase();
    if (!typed) return null;
    const match = state.items.find((item) => (
      displayText(item).toLowerCase() === typed
      || (exactShortcuts?.(item) || []).some((value) => value.toLowerCase() === typed)
    ));
    if (match) {
      state.selected = match;
      input.value = displayText(match);
      onSelect?.(match);
    }
    return match || null;
  }

  return {
    setItems(items) {
      state.items = items;
      state.filtered = items.slice();
      state.activeIndex = -1;
      state.selected = null;
      input.value = "";
      setOpen(false);
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
    tryPromoteTypedSelection: promoteTypedSelection,
  };
}
