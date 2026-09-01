import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

export type StatusBadgeTone = 'indigo' | 'amber' | 'emerald' | 'slate' | 'red';
export type StatusBadgeSize = 'xs' | 'sm';

const TONE_CLASSES: Record<StatusBadgeTone, string> = {
  indigo: 'bg-indigo-50 dark:bg-indigo-950 text-indigo-700 dark:text-indigo-300 border-indigo-200 dark:border-indigo-800',
  amber: 'bg-amber-50 dark:bg-amber-950 text-amber-700 dark:text-amber-300 border-amber-200 dark:border-amber-800',
  emerald: 'bg-emerald-50 dark:bg-emerald-950 text-emerald-700 dark:text-emerald-300 border-emerald-200 dark:border-emerald-800',
  slate: 'bg-slate-50 dark:bg-slate-800 text-slate-500 dark:text-slate-400 border-slate-200 dark:border-slate-700',
  red: 'bg-red-50 dark:bg-red-950 text-red-700 dark:text-red-300 border-red-200 dark:border-red-800',
};

const SIZE_CLASSES: Record<StatusBadgeSize, string> = {
  xs: 'text-[10px] px-1.5 py-0.5',
  sm: 'text-xs px-2 py-0.5',
};

/** One shared pill for status/count badges -- collapses the size (10px/
 * 11px/xs) and border-presence drift found across the advisor dashboard,
 * flowchart, demo-login, and recommendations pages into two named sizes
 * and a fixed tone palette. Skip this component for a badge that doesn't
 * cleanly fit the tone enum rather than forcing it in. */
@Component({
  selector: 'app-status-badge',
  standalone: true,
  templateUrl: './status-badge.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class StatusBadgeComponent {
  label = input.required<string>();
  tone = input<StatusBadgeTone>('slate');
  size = input<StatusBadgeSize>('sm');

  classes = computed(
    () =>
      `inline-flex items-center gap-1 font-semibold uppercase tracking-wide rounded-full border ${TONE_CLASSES[this.tone()]} ${SIZE_CLASSES[this.size()]}`
  );
}
