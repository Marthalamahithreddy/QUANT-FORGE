import type { ReactNode } from "react";

import { CONTROL } from "@/components/ui/controls";

interface FieldBase {
  readonly id: string;
  readonly name?: string;
  readonly label: ReactNode;
  /** Marks the control invalid and points it at the element that explains why. */
  readonly invalid?: boolean;
  readonly describedBy?: string;
}

export interface TextFieldProps extends FieldBase {
  readonly type?: "text" | "date";
  readonly defaultValue?: string;
  readonly maxLength?: number;
  readonly inputMode?: "numeric" | "text";
  readonly pattern?: string;
  readonly title?: string;
  /** Identifier-like values (tickers, CIKs) use the monospace role. */
  readonly mono?: boolean;
  /** Tailwind width class for the control, e.g. `w-32`. */
  readonly width?: string;
}

/** A labelled native text or date input. The label is always visible and tied to the control. */
export function TextField({
  id,
  name = id,
  label,
  type = "text",
  defaultValue,
  maxLength,
  inputMode,
  pattern,
  title,
  mono = false,
  width = "w-40",
  invalid = false,
  describedBy,
}: TextFieldProps) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="type-label">
        {label}
      </label>
      <input
        id={id}
        name={name}
        type={type}
        defaultValue={defaultValue}
        maxLength={maxLength}
        inputMode={inputMode}
        pattern={pattern}
        title={title}
        autoComplete="off"
        aria-invalid={invalid || undefined}
        aria-describedby={invalid ? describedBy : undefined}
        className={`${CONTROL} ${width} ${mono ? "type-id" : ""}`}
      />
    </div>
  );
}

export interface SelectFieldProps extends FieldBase {
  readonly defaultValue?: string;
  readonly required?: boolean;
  readonly children: ReactNode;
}

/** A labelled native select. */
export function SelectField({
  id,
  name = id,
  label,
  defaultValue,
  required,
  invalid = false,
  describedBy,
  children,
}: SelectFieldProps) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="type-label">
        {label}
      </label>
      <select
        id={id}
        name={name}
        required={required}
        defaultValue={defaultValue}
        aria-invalid={invalid || undefined}
        aria-describedby={invalid ? describedBy : undefined}
        className={CONTROL}
      >
        {children}
      </select>
    </div>
  );
}
