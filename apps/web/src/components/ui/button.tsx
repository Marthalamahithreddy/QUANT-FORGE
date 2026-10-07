import { AppLink } from "@/components/ui/app-link";
import type { ButtonHTMLAttributes, ReactNode } from "react";

import { BUTTON_BASE, BUTTON_VARIANT, type ButtonVariant } from "@/components/ui/controls";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  readonly variant?: ButtonVariant;
}

/** A native button. Server-compatible: it carries no state of its own. */
export function Button({ variant = "secondary", className = "", type = "button", ...rest }: ButtonProps) {
  return <button type={type} className={`${BUTTON_BASE} ${BUTTON_VARIANT[variant]} ${className}`} {...rest} />;
}

export interface LinkButtonProps {
  readonly href: string;
  readonly variant?: ButtonVariant;
  /** An accessible name that carries context a bare label lacks ("Research Acme, 2024-03-15"). */
  readonly label?: string;
  readonly children: ReactNode;
}

/** A link that looks like a button (navigation, not an action). */
export function LinkButton({ href, variant = "secondary", label, children }: LinkButtonProps) {
  return (
    <AppLink href={href} aria-label={label} className={`${BUTTON_BASE} ${BUTTON_VARIANT[variant]}`}>
      {children}
    </AppLink>
  );
}
