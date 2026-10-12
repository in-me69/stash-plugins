// Scene Reset: adds "Reset metadata…" to
// - the scene list's menu (the one with Identify and Generate), for the
//   selected scenes, and
// - the "…" menu on a scene's own page, for that scene.
//
// Stash doesn't let plugins add items to these menus directly, so this watches
// the page and adds the item when a menu is shown. On the scene list the menu
// has the class "scene-list-operations-dropdown" and the selected scenes are
// read from the checked cards (grid view) or rows (list view). On a scene page
// the menu belongs to the "operation-menu" toggle and the scene ID comes from
// the URL. The work itself is done by the plugin's "Reset scenes" task, queued
// with the scene IDs.
(function () {
  "use strict";

  const PLUGIN_ID = "SceneReset";
  const TASK_NAME = "Reset scenes";
  const LIST_MENU_SELECTOR = ".scene-list-operations-dropdown";
  const SCENE_PAGE_TOGGLE_ID = "operation-menu";
  const ITEM_CLASS = "scene-reset-item";

  function graphqlUrl() {
    // Stash may be served under a sub-path; <base href> points at its root.
    return new URL("graphql", document.baseURI).toString();
  }

  async function graphql(query, variables) {
    const response = await fetch(graphqlUrl(), {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, variables }),
    });
    const payload = await response.json();
    if (payload.errors) {
      throw new Error(payload.errors.map((e) => e.message).join("; "));
    }
    return payload.data;
  }

  function sceneIdFrom(element) {
    const link = element.querySelector('a[href*="/scenes/"]');
    const match = link && link.getAttribute("href").match(/\/scenes\/(\d+)/);
    return match ? match[1] : null;
  }

  function selectedSceneIds() {
    const ids = new Set();
    // Grid view: each card has a "card-check" checkbox.
    document.querySelectorAll(".grid-card input.card-check:checked").forEach((box) => {
      const id = sceneIdFrom(box.closest(".grid-card"));
      if (id) ids.add(id);
    });
    // List view: each row has a checkbox in its "select-col" cell.
    document.querySelectorAll("td.select-col input[type=checkbox]:checked").forEach((box) => {
      const id = sceneIdFrom(box.closest("tr"));
      if (id) ids.add(id);
    });
    return Array.from(ids);
  }

  function toast(message, isError) {
    const el = document.createElement("div");
    el.textContent = message;
    el.style.cssText =
      "position:fixed;bottom:1rem;right:1rem;z-index:2000;padding:.75rem 1rem;" +
      "border-radius:.25rem;color:#fff;max-width:28rem;box-shadow:0 2px 8px rgba(0,0,0,.4);" +
      `background:${isError ? "#c0392b" : "#2e7d32"}`;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), isError ? 8000 : 4000);
  }

  // The scene ID when on a scene's own page (/scenes/<id>, perhaps under a
  // sub-path), otherwise null.
  function currentPageSceneId() {
    const match = window.location.pathname.match(/\/scenes\/(\d+)\/?$/);
    return match ? match[1] : null;
  }

  async function resetScenes(ids, whenDone) {
    if (ids.length === 0) {
      toast("Select some scenes first.", true);
      return;
    }
    const noun = ids.length === 1 ? "this scene" : `${ids.length} scenes`;
    const ok = window.confirm(
      `Reset ${noun}?\n\n` +
        "This removes the title, date, code, details, director, URLs, studio, " +
        "performers, tags, groups and stash-box IDs, unticks Organized, and " +
        "resets the cover (a folder cover/poster image if Folder Media finds one, " +
        "otherwise a new screenshot).\n\nFiles, play history, rating and markers are kept. " +
        "This can't be undone."
    );
    if (!ok) return;
    try {
      await graphql(
        "mutation($id: ID!, $task: String!, $desc: String, $args: Map) {" +
          " runPluginTask(plugin_id: $id, task_name: $task, description: $desc, args_map: $args) }",
        {
          id: PLUGIN_ID,
          task: TASK_NAME,
          desc: ids.length === 1 ? `Resetting scene ${ids[0]}` : `Resetting ${ids.length} scenes`,
          args: { scene_ids: ids.join(",") },
        }
      );
      toast(`Reset queued for ${noun}. Progress is on the Tasks page; ${whenDone} when it's done.`);
    } catch (e) {
      toast(`Couldn't start the reset: ${e.message}`, true);
    }
  }

  // Adds the item to `menu` (once), after the first item whose text starts
  // with `after` if there is one, otherwise at the end.
  function addItem(menu, after, onClick) {
    if (menu.querySelector(`.${ITEM_CLASS}`)) return;
    const item = document.createElement("a");
    item.href = "#";
    item.className = `dropdown-item bg-secondary text-white ${ITEM_CLASS}`;
    item.textContent = "Reset metadata…";
    item.addEventListener("click", (event) => {
      event.preventDefault();
      // Close the menu the way Bootstrap does, by toggling it again.
      const toggle = menu.parentElement && menu.parentElement.querySelector(".dropdown-toggle");
      if (toggle && menu.classList.contains("show")) toggle.click();
      onClick();
    });
    const items = Array.from(menu.querySelectorAll(".dropdown-item"));
    const anchor = items.find((el) => el.textContent.trim().startsWith(after));
    if (anchor) anchor.after(item);
    else menu.appendChild(item);
  }

  function refreshListMenus() {
    document.querySelectorAll(LIST_MENU_SELECTOR).forEach((menu) => {
      const existing = menu.querySelector(`.${ITEM_CLASS}`);
      if (selectedSceneIds().length > 0) {
        // Next to Identify (only shown with a selection).
        addItem(menu, "Identify", () => resetScenes(selectedSceneIds(), "refresh the list"));
      } else if (existing) {
        existing.remove();
      }
    });
  }

  function refreshScenePageMenu() {
    const sceneId = currentPageSceneId();
    const toggle = document.getElementById(SCENE_PAGE_TOGGLE_ID);
    if (!sceneId || !toggle) return;
    const dropdown = toggle.closest(".dropdown");
    const menu = dropdown && dropdown.querySelector(".dropdown-menu");
    if (!menu) return; // Bootstrap only renders the menu once it's first opened
    // Next to Generate. The ID is read at click time, since Stash reuses the
    // page (and this menu) when moving between scenes.
    addItem(menu, "Generate", () => {
      const id = currentPageSceneId();
      if (id) resetScenes([id], "reload this page");
    });
  }

  function refresh() {
    refreshListMenus();
    refreshScenePageMenu();
  }

  // React re-renders the menu (and the selection) freely, so re-check on every change.
  let scheduled = false;
  new MutationObserver(() => {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false;
      refresh();
    });
  }).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["class"] });
  document.addEventListener("change", refresh, true);
  refresh(); // in case a menu is already open when this loads
})();
