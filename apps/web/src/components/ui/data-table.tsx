import type { ReactNode } from "react";

/** Column priority (spec section 10.7.2): P1 always, P2 from 768px, P3 from 1280px. */
export type ColumnPriority = "P1" | "P2" | "P3";

export interface DataTableColumn<Row> {
  readonly id: string;
  readonly header: string;
  readonly cell: (row: Row, index: number) => ReactNode;
  /** `end` right-aligns a numeric column and gives it tabular figures. Default `start`. */
  readonly align?: "start" | "end";
  readonly priority?: ColumnPriority;
}

export interface DataTableProps<Row> {
  /** The table's title and accessible name. */
  readonly caption: string;
  /**
   * Keep the caption for assistive technology only. For a table that sits directly under a visible
   * heading saying the same thing, so the title is not shown twice.
   */
  readonly captionHidden?: boolean;
  /** The FIRST column is the identity column: a row header, sticky while scrolling sideways. */
  readonly columns: readonly DataTableColumn<Row>[];
  readonly rows: readonly Row[];
  readonly rowKey: (row: Row, index: number) => string;
  /** Shown instead of the table when there are no rows (an `EmptyState`, never fake rows). */
  readonly empty?: ReactNode;
}

const PRIORITY_CLASS: Record<ColumnPriority, string> = {
  P1: "",
  P2: "hidden md:table-cell",
  P3: "hidden xl:table-cell",
};

const CELL = "border-b border-border-subtle px-3 py-1.5 align-top";

/**
 * The one dense table: a real `<table>` with a caption, `scope` headers and a row header per row.
 * It is a server component with NO client sorting or filtering: order and filters are the API's,
 * so the page can never present a sorted view of one page as if it were the dataset. The
 * identity link lives in the first cell (no whole-row click targets), the table scrolls in a
 * focusable, labelled region, and secondary columns drop by priority instead of stacking into
 * cards.
 */
export function DataTable<Row>({ caption, captionHidden = false, columns, rows, rowKey, empty }: DataTableProps<Row>) {
  if (rows.length === 0 && empty !== undefined) return <>{empty}</>;

  const hiddenBelowXl = columns.filter((column) => column.priority === "P3").length;
  const hiddenBelowMd = hiddenBelowXl + columns.filter((column) => column.priority === "P2").length;

  return (
    <div className="flex flex-col gap-1.5">
      <div
        // Focusable, so a keyboard user can scroll the rows and the wide columns.
        tabIndex={0}
        role="region"
        aria-label={`${caption}, scrollable`}
        // `relative` makes the region the containing block of the visually-hidden (absolutely positioned)
        // state names inside cells; otherwise they sit at their static position in the wide table and
        // widen the whole page.
        className="relative max-h-[70vh] overflow-auto rounded border border-border"
      >
        <table className="w-full border-separate border-spacing-0 text-left">
          <caption className={captionHidden ? "sr-only" : "type-section border-b border-border-subtle px-3 py-2 text-left"}>{caption}</caption>
          <thead>
            <tr>
              {columns.map((column, index) => (
                <th
                  key={column.id}
                  scope="col"
                  className={`sticky top-0 border-b border-border bg-surface-elevated px-3 py-2 text-xs font-medium whitespace-nowrap text-text-3 ${
                    index === 0 ? "left-0 z-30 border-r" : "z-20"
                  } ${column.align === "end" ? "text-right" : ""} ${PRIORITY_CLASS[column.priority ?? "P1"]}`}
                >
                  {column.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="type-table">
            {rows.map((row, rowIndex) => (
              <tr key={rowKey(row, rowIndex)} className="group">
                {columns.map((column, index) => {
                  const classes = `${CELL} ${column.align === "end" ? "num text-right" : ""} ${PRIORITY_CLASS[column.priority ?? "P1"]}`;
                  return index === 0 ? (
                    <th
                      key={column.id}
                      scope="row"
                      className={`${CELL} sticky left-0 z-10 border-r border-r-border-subtle bg-surface text-left font-normal whitespace-nowrap group-hover:bg-hover`}
                    >
                      {column.cell(row, rowIndex)}
                    </th>
                  ) : (
                    <td key={column.id} className={`${classes} group-hover:bg-hover`}>
                      {column.cell(row, rowIndex)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {hiddenBelowMd > 0 ? (
        <>
          <p data-testid="columns-note-narrow" className="type-meta md:hidden">
            {hiddenBelowMd} more column{hiddenBelowMd === 1 ? "" : "s"} at wider widths.
          </p>
          {hiddenBelowXl > 0 ? (
            <p data-testid="columns-note-medium" className="type-meta hidden md:block xl:hidden">
              {hiddenBelowXl} more column{hiddenBelowXl === 1 ? "" : "s"} at wider widths.
            </p>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
