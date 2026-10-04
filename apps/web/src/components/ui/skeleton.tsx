import type { CSSProperties, ReactNode } from "react";

export interface SkeletonProps {
  readonly className?: string;
  /** Final dimensions of the content this stands in for, so nothing jumps when it arrives. */
  readonly width?: CSSProperties["width"];
  readonly height?: CSSProperties["height"];
}

/** A placeholder block. Decorative: hide it from assistive technology and describe the region. */
export function Skeleton({ className = "", width, height = "1rem" }: SkeletonProps) {
  return (
    <div
      aria-hidden="true"
      className={`qf-skeleton rounded bg-surface-raised ${className}`}
      style={{ width, height }}
    />
  );
}

export interface SkeletonRegionProps {
  /** What is loading, announced politely ("Loading earnings events"). */
  readonly label: string;
  readonly children: ReactNode;
}

/** Marks a region as busy and gives it one accessible description; never a full-page spinner. */
export function SkeletonRegion({ label, children }: SkeletonRegionProps) {
  return (
    <div role="status" aria-busy="true" className="flex flex-col gap-3">
      <span className="sr-only">{label}</span>
      {children}
    </div>
  );
}
