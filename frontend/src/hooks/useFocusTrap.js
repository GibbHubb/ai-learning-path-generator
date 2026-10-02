// AP40 — the five behaviours a modal dialog owes a keyboard user, in one place
// so the next overlay does not hand-roll a second, diverging copy:
//
//   1. on open, focus moves INTO the dialog (the container itself, so a
//      screen reader announces the dialog's label before anything else);
//   2. Tab / Shift+Tab cycle inside it and never escape to the page behind,
//      which `aria-modal="true"` has told assistive tech is inert;
//   3. Escape closes it;
//   4. a pointer press outside it closes it (the old overlay onClick);
//   5. on close, focus returns to whatever had it before — normally the
//      button that opened the dialog — instead of falling to <body>. If that
//      element is gone (passing the quiz completes the milestone, which removes
//      the "Take quiz" button), focus goes to `fallbackId` instead.
//
// `onClose` is read through a ref, so callers can pass an inline arrow without
// re-running the effect (and re-stealing focus) on every render.

import { useEffect, useRef } from 'react';

const FOCUSABLE = [
    'a[href]',
    'button:not([disabled])',
    'input:not([disabled]):not([type="hidden"])',
    'select:not([disabled])',
    'textarea:not([disabled])',
    '[tabindex]:not([tabindex="-1"])',
].join(',');

function focusables(container) {
    return Array.from(container.querySelectorAll(FOCUSABLE))
        .filter((el) => !el.closest('[hidden]') && el.getClientRects().length > 0);
}

export default function useFocusTrap(containerRef, onClose, fallbackId) {
    const onCloseRef = useRef(onClose);
    onCloseRef.current = onClose;
    const fallbackRef = useRef(fallbackId);
    fallbackRef.current = fallbackId;
    // Captured during the FIRST render, before the effect moves focus into the
    // dialog. Reading document.activeElement inside the effect instead breaks
    // under React StrictMode (dev), whose mount-unmount-remount means the
    // second effect sees the dialog itself as "previously focused".
    const returnToRef = useRef(null);
    if (returnToRef.current === null) returnToRef.current = document.activeElement;

    useEffect(() => {
        const container = containerRef.current;
        if (!container) return undefined;

        const previouslyFocused = returnToRef.current;
        container.focus();

        const onKeyDown = (e) => {
            if (e.key === 'Escape') {
                e.preventDefault();
                e.stopPropagation();
                onCloseRef.current && onCloseRef.current();
                return;
            }
            if (e.key !== 'Tab') return;
            const items = focusables(container);
            if (items.length === 0) {
                e.preventDefault();
                container.focus();
                return;
            }
            const first = items[0];
            const last = items[items.length - 1];
            const active = document.activeElement;
            const inside = container.contains(active);
            if (e.shiftKey && (active === first || active === container || !inside)) {
                e.preventDefault();
                last.focus();
            } else if (!e.shiftKey && (active === last || !inside)) {
                e.preventDefault();
                first.focus();
            }
        };

        // Focus can still leave by other means (a click on the page behind, a
        // screen reader's virtual cursor); pull it back.
        const onFocusIn = (e) => {
            if (!container.contains(e.target)) container.focus();
        };

        const onPointerDown = (e) => {
            if (!container.contains(e.target)) onCloseRef.current && onCloseRef.current();
        };

        document.addEventListener('keydown', onKeyDown, true);
        document.addEventListener('focusin', onFocusIn);
        document.addEventListener('pointerdown', onPointerDown);
        return () => {
            document.removeEventListener('keydown', onKeyDown, true);
            document.removeEventListener('focusin', onFocusIn);
            document.removeEventListener('pointerdown', onPointerDown);
            // Deferred one frame: the parent's re-render (which may remove the
            // trigger) has to land before we can tell whether it still exists.
            requestAnimationFrame(() => {
                // <body> is not a real return target: Safari does not focus a
                // button on mouse click, so a pointer-opened dialog sees body here.
                const isRealTarget = previouslyFocused
                    && previouslyFocused !== document.body
                    && previouslyFocused !== document.documentElement;
                if (isRealTarget && typeof previouslyFocused.focus === 'function'
                    && document.contains(previouslyFocused)) {
                    previouslyFocused.focus();
                    return;
                }
                const fallback = fallbackRef.current && document.getElementById(fallbackRef.current);
                if (fallback) fallback.focus();
            });
        };
    }, [containerRef]);
}
