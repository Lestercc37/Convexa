"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

/**
 * A v2 dropdown (docs/maqueta_app_escritorio_v2_2026-10-27.html: "Ventanas" in the top bar, Niveles / Gamma / Flujo / Dibujo on the chart): a button with an optional count,
 * and a panel fixed under it. The panel is rendered in document.body (a portal): the bar's rows have a fade mask and sideways scrolling, and a mask hides
 * everything drawn inside its element, so a "Ventanas" panel drawn inside the second row was invisible and the button seemed to do nothing (Lester
 * 2026-10-30). Closes on a click outside, on Escape and when the window resizes.
 */
export function V2Dropdown({
  label,
  count,
  align = "left",
  className,
  children,
}: {
  label: ReactNode;
  count?: number;
  align?: "left" | "right";
  className?: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [place, setPlace] = useState<{ top: number; left?: number; right?: number }>({ top: 0 });
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuId = useId();

  useEffect(() => {
    if (!open) return;
    const close = (event: Event) => {
      const target = event.target as Node | null;
      if (target && (menuRef.current?.contains(target) || buttonRef.current?.contains(target))) return;
      setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    const onResize = () => setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", onResize);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", onResize);
    };
  }, [open]);

  const toggle = () => {
    const rect = buttonRef.current?.getBoundingClientRect();
    if (rect) {
      setPlace(align === "right" ? { top: rect.bottom + 6, right: Math.max(8, window.innerWidth - rect.right) } : { top: rect.bottom + 6, left: Math.max(8, rect.left) });
    }
    setOpen((current) => !current);
  };

  return (
    <div className={`v2-dd ${className ?? ""}`}>
      <button ref={buttonRef} type="button" aria-expanded={open} aria-controls={menuId} onClick={toggle}>
        {label}
        {count !== undefined && <span className="v2-cnt">{count}</span>}
        {count === undefined && <span aria-hidden="true"> ▾</span>}
      </button>
      {open &&
        createPortal(
          // Inside .app-v2 styling scope: the portal target carries the class so the menu keeps the v2 look and fonts.
          <div ref={menuRef} id={menuId} className={`v2-menu app-v2-portal ${className ?? ""}`} role="group" style={place}>
            {children}
          </div>,
          document.querySelector(".app-v2") ?? document.body,
        )}
    </div>
  );
}
