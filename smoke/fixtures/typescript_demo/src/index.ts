import { add, multiply } from "./math";

export function computeScore(base: number, weight: number): number {
  return multiply(add(base, 2), weight);
}

export function formatScore(score: number): string {
  return `score:${score}`;
}
