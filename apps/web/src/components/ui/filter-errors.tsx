import { ErrorState } from "@/components/ui/error-state";
import type { ListError } from "@/lib/list-query";

/**
 * The local validation failure of a list's URL: shown BEFORE any request, naming each rejected
 * parameter. Controls point at it with `aria-describedby`, so the explanation is associated with
 * the field that caused it.
 */
export function FilterErrors({
  id,
  errors,
  title = "These filters cannot be used. No request was made.",
}: {
  readonly id: string;
  readonly errors: readonly ListError[];
  readonly title?: string;
}) {
  return (
    <ErrorState id={id} title={title}>
      <ul className="list-disc pl-5">
        {errors.map((error) => (
          <li key={error.param}>{error.message}</li>
        ))}
      </ul>
    </ErrorState>
  );
}

export const hasError = (errors: readonly ListError[], param: string): boolean =>
  errors.some((error) => error.param === param);
