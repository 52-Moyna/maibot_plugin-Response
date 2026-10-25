/* 插件配置页增强（纯浏览器脚本，不依赖任何操作系统）：
   1) 给「插件配置」路由打标，供 mb-plugin-config.css 作用域使用；
   2) 把滑条右侧的只读数值改成可手动填写的输入框，并保证双向同步。

   关于第 2 点：框架把数值渲染成 <span>，真正的值存在 React state 里，
   改 DOM 文本不会写回配置。所以输入数字后，这里通过给滑条 thumb 派发
   键盘事件（Home / ArrowRight）来驱动框架自己的 onChange，滑条位置与
   配置值都会跟着变 —— 等同于用户用键盘操作滑条。
   注意 Radix 的 aria-valuenow 是**异步**更新的，每次按键后必须等一帧再读，
   否则读到的是旧值（这是第一版没生效的原因）。
   反向同步用 MutationObserver 观察 thumb 的 aria-valuenow。 */
(() => {
  "use strict";

  const ATTR = "mbPluginConfig";
  const root = document.documentElement;
  const INPUT_CLASS = "mb-slider-input";
  const BOUND_FLAG = "mbSliderInput";

  /* ---------------- 1. 路由打标 ---------------- */

  function mark() {
    const on = location.pathname.startsWith("/plugin-config");
    if (on) {
      if (root.dataset[ATTR] !== "1") root.dataset[ATTR] = "1";
    } else if (ATTR in root.dataset) {
      delete root.dataset[ATTR];
    }
  }

  ["pushState", "replaceState"].forEach((key) => {
    const original = history[key];
    if (typeof original !== "function") return;
    history[key] = function patched() {
      const result = original.apply(this, arguments);
      setTimeout(mark, 0);
      return result;
    };
  });

  window.addEventListener("popstate", mark);
  document.addEventListener("DOMContentLoaded", mark);
  mark();
  setInterval(mark, 700);

  /* ---------------- 2. 滑条数值可手动输入 ---------------- */

  const num = (el, attr) => {
    const v = parseFloat(el.getAttribute(attr));
    return Number.isFinite(v) ? v : null;
  };

  const formatValue = (raw) => {
    const v = parseFloat(raw);
    if (!Number.isFinite(v)) return String(raw ?? "");
    // 去掉浮点噪音（0.30000000000000004 → 0.3）
    return String(Number(v.toFixed(6)));
  };

  // 等 React 完成一次渲染再继续（Radix 的值更新不是同步的）
  const nextTick = () => new Promise((resolve) => requestAnimationFrame(() => setTimeout(resolve, 24)));

  const dispatchKey = (el, key) => {
    el.dispatchEvent(
      new KeyboardEvent("keydown", {
        key,
        code: key,
        bubbles: true,
        cancelable: true,
        composed: true,
      })
    );
  };

  const pressKey = async (el, key) => {
    dispatchKey(el, key);
    await nextTick();
  };

  /** 用键盘事件把滑条推到目标值（自动探测步长），返回是否到达目标。 */
  async function applyValue(thumb, target) {
    const min = num(thumb, "aria-valuemin");
    const max = num(thumb, "aria-valuemax");
    if (min === null || max === null) return false;

    const wanted = Math.min(max, Math.max(min, target));

    thumb.focus({ preventScroll: true });

    // 1) 归零并确认
    await pressKey(thumb, "Home");
    const base = num(thumb, "aria-valuenow");
    if (base === null) return false;

    // 2) 探一步，得到 step
    await pressKey(thumb, "ArrowRight");
    const step = (num(thumb, "aria-valuenow") ?? base) - base;
    if (!(step > 0)) return false;

    // 3) 多步用 PageUp（Radix 每次走 10 个 step），剩下用方向键补
    const total = Math.round((wanted - base) / step);
    await pressKey(thumb, "Home");
    const pages = Math.floor(total / 10);
    for (let i = 0; i < Math.min(pages, 50); i += 1) {
      await pressKey(thumb, "PageUp");
    }
    for (let i = 0; i < total % 10; i += 1) {
      await pressKey(thumb, "ArrowRight");
    }

    // 4) 收敛校验：浮点误差或框架步长差异都靠这一步兜住
    for (let guard = 0; guard < 40; guard += 1) {
      const cur = num(thumb, "aria-valuenow");
      if (cur === null) return false;
      if (Math.abs(cur - wanted) <= step / 2 + 1e-9) return true;
      await pressKey(thumb, cur < wanted ? "ArrowRight" : "ArrowLeft");
    }

    const got = num(thumb, "aria-valuenow");
    return got !== null && Math.abs(got - wanted) <= step / 2 + 1e-9;
  }

  function bindField(wrapper, thumb, readout) {
    if (wrapper.dataset[BOUND_FLAG] === "1") return;
    wrapper.dataset[BOUND_FLAG] = "1";

    const input = document.createElement("input");
    input.type = "text";
    input.inputMode = "decimal";
    input.autocomplete = "off";
    input.spellcheck = false;
    input.className = INPUT_CLASS;
    input.value = formatValue(thumb.getAttribute("aria-valuenow"));
    input.setAttribute("aria-label", "可直接输入数值，回车生效");

    readout.replaceWith(input);

    let busy = false;
    let queue = Promise.resolve();

    const syncFromSlider = () => {
      const latest = formatValue(thumb.getAttribute("aria-valuenow"));
      if (input.value !== latest) input.value = latest;
    };

    // 连续输入（比如回车后立刻又改）排队执行，避免前一次还没跑完后一次被丢掉
    const commit = () => {
      const text = String(input.value).replace(/[^\d.\-]/g, "");
      const parsed = parseFloat(text);
      queue = queue.then(async () => {
        if (!Number.isFinite(parsed)) {
          syncFromSlider();
          return;
        }
        if (busy) return;
        busy = true;
        try {
          await applyValue(thumb, parsed);
        } catch (err) {
          console.warn("[mb] 设置滑条数值失败", err);
        } finally {
          busy = false;
          syncFromSlider();
        }
      });
      return queue;
    };

    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        commit();
      } else if (e.key === "Escape") {
        e.preventDefault();
        input.value = formatValue(thumb.getAttribute("aria-valuenow"));
        input.blur();
      } else if (e.key === "ArrowUp" || e.key === "ArrowDown") {
        // 上下键微调，交给滑条处理更省事：先提交再让滑条接住方向键
        e.preventDefault();
        input.blur();
        thumb.focus({ preventScroll: true });
        dispatchKey(thumb, e.key === "ArrowUp" ? "ArrowRight" : "ArrowLeft");
      }
    });

    input.addEventListener("blur", () => {
      commit();
    });

    // 滑条被拖动 / 键盘操作时，回填输入框
    const observer = new MutationObserver(() => {
      if (busy) return;
      if (document.activeElement === input) return;
      syncFromSlider();
    });
    observer.observe(thumb, { attributes: true, attributeFilter: ["aria-valuenow"] });
  }

  function enhance(scope) {
    const thumbs = scope.querySelectorAll("[data-dashboard-slider-thumb]");
    thumbs.forEach((thumb) => {
      const wrapper = thumb.closest(".space-y-2");
      if (!wrapper) return;
      if (wrapper.dataset[BOUND_FLAG] === "1") return;

      const head = wrapper.querySelector(":scope > .flex.items-center.justify-between");
      if (!head) return;
      const readout = head.querySelector(":scope > span");
      if (!readout) return; // 已经是输入框或被改过

      bindField(wrapper, thumb, readout);
    });
  }

  function scan() {
    if (!location.pathname.startsWith("/plugin-config")) return;
    enhance(document);
  }

  // 配置页是动态渲染的，且切换插件会重建 DOM，这里对整页做增量观察
  let scanScheduled = false;
  const pageObserver = new MutationObserver(() => {
    if (scanScheduled) return;
    scanScheduled = true;
    setTimeout(() => {
      scanScheduled = false;
      scan();
    }, 120);
  });

  const start = () => {
    pageObserver.observe(document.body, { childList: true, subtree: true });
    scan();
  };

  document.addEventListener("DOMContentLoaded", scan);
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
