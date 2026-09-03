import { ChangeDetectionStrategy, Component, computed, signal } from '@angular/core';

interface FaqEntry {
  q: string;
  a: string;
}

interface FaqCategory {
  title: string;
  entries: FaqEntry[];
}

/** Answers the questions students actually ask the chat over and over --
 * how recommendations work, whether seats are real, how the waitlist
 * behaves, what the social features do and don't share. A student who
 * reads this instead of asking the chat gets the same answer faster, and
 * it costs nothing to serve (no LLM call). Content lives directly in this
 * file rather than a CMS/backend endpoint -- it changes about as often as
 * the app's own features do, so a code change is the right way to update
 * it. */
@Component({
  selector: 'app-faq-page',
  standalone: true,
  templateUrl: './faq-page.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class FaqPageComponent {
  query = signal('');

  readonly categories: FaqCategory[] = [
    {
      title: 'Planning & recommendations',
      entries: [
        {
          q: 'How does Planny decide what I should take next?',
          a: "A deterministic planning engine — not the chat AI — walks your completed courses against your major’s real prerequisite and credit-requirement data and computes what’s actually unlocked. The chat’s replies just put that computed list into words; it never invents a course or changes what’s recommended.",
        },
        {
          q: "It recommended a course I’ve already taken — what’s wrong?",
          a: 'Check that the course is actually marked completed on your Flowchart or Progress page, and that the course code matches exactly (e.g. "CMPSC 131" vs "CMPSC131"). If you uploaded a transcript, a course can occasionally be misread — you can always add or correct it by typing it directly in chat.',
        },
        {
          q: 'What does "ETM" mean on a course card?',
          a: 'Entrance to Major — a small set of courses your major treats as a gate, usually needing a specific grade, before you can formally declare that major. Missing an ETM course doesn\'t block your other progress, but it\'s worth prioritizing early.',
        },
        {
          q: 'Can I take two courses at the same time (concurrent)?',
          a: "Some prerequisite chains explicitly allow it (e.g. a course listed as \"MATH 140 concurrent with CHEM 110\") — the planner already accounts for this when it’s true for a given requirement. If a course you want shows as blocked, it means that specific prerequisite doesn’t allow concurrent enrollment.",
        },
        {
          q: "My major isn’t listed, or I’m not sure what I want yet — what do I do?",
          a: 'Mark yourself "I\'m undecided" during setup — the chat switches to an exploration mode where you can describe what you enjoy and get major suggestions, with no schedule generated until you pick one.',
        },
      ],
    },
    {
      title: 'Your plan & progress',
      entries: [
        {
          q: 'How is "on pace to graduate" calculated?',
          a: 'From your remaining requirements, your chosen credits-per-semester load, and whether you allow summer courses — all real settings you control on Your Plan, not a guess.',
        },
        {
          q: 'Can I have more than one plan?',
          a: 'Yes, if you create a free account (optional — nothing requires it). Signed-in students can save and switch between multiple named plans, e.g. to compare "what if I add a minor" without losing your main plan.',
        },
        {
          q: 'Does uploading my transcript replace typing courses in?',
          a: "It’s a shortcut, not a replacement — it pre-fills your completed courses from a PDF instead of you typing each one. You can still add, remove, or correct individual courses afterward the same way either way.",
        },
        {
          q: "What happens to my plan if I close the tab?",
          a: "If you never created an account, nothing is saved anywhere — it exists only in that browser tab. Sign in (still optional, still free) and it autosaves as you go, so it’s there the next time you visit.",
        },
      ],
    },
    {
      title: 'Real seats & the waitlist',
      entries: [
        {
          q: 'Are the seat counts on the Weekly Schedule real?',
          a: 'The sample meeting times and the "Sample seat availability" line are illustrative — Penn State doesn\'t publish real per-section times or counts this far out. The separate "Real seat, held for you" section below it is genuinely real: a shared, database-tracked seat pool other signed-in students are applying against too.',
        },
        {
          q: 'How does the waitlist work — do I need to keep refreshing?',
          a: "No. If a course is full when you apply, you’re placed on a real waitlist in the order you applied. The moment someone with a seat drops it, the system automatically promotes whoever has been waiting longest — you don’t have to do anything or watch for it.",
        },
        {
          q: 'Can two people accidentally get the same seat, or can I apply twice by accident?',
          a: "No to both — enforced by the database itself, not just the app. A course can never seat more students than its capacity even under heavy simultaneous demand, and a student can only ever hold one allocation (enrolled or waitlisted) per course; re-applying just confirms your existing status instead of creating a second one.",
        },
        {
          q: 'What happens if I drop a course?',
          a: 'Your seat is released immediately, and if anyone is waitlisted, the longest-waiting student is automatically promoted into it — the same thing happens if you delete your account while enrolled, so a seat never sits reserved-but-abandoned.',
        },
      ],
    },
    {
      title: 'Groups & classmate networking',
      entries: [
        {
          q: '"Take it with friends" — what is it, and can other students find me through it?',
          a: "It’s a group tied to one course, joined only via an invite code you share yourself (text, email, whatever you’d normally use) — there’s no student directory or search anywhere in the app, so nobody can \"find\" you through it. Fellow group members see an anonymous headcount and how many have a seat, never who’s who beyond people you already invited.",
        },
        {
          q: 'How does opt-in LinkedIn sharing work — is it public?',
          a: "It’s off by default and stays off until you turn it on yourself. Even then, it’s never a public directory: another student can only see your LinkedIn if you opted in and you’re both actually enrolled in the same real course.",
        },
      ],
    },
    {
      title: 'Advisors & privacy',
      entries: [
        {
          q: 'Can I talk to a real human advisor?',
          a: 'Yes — "Request advisor review" on Your Plan sends your plan to a real advisor for comments and meeting scheduling. The chat AI is meant for quick, everyday planning questions, not a replacement for that relationship.',
        },
        {
          q: 'Do I need an account, and is my data private?',
          a: "An account is entirely optional. Without one, nothing about you is stored. With one, only what’s needed to run the features you actually use is kept, and you can permanently delete your account and everything tied to it at any time from the account menu — see the Privacy Policy for the full detail.",
        },
      ],
    },
  ];

  readonly filteredCategories = computed(() => {
    const q = this.query().trim().toLowerCase();
    if (!q) return this.categories;
    return this.categories
      .map((cat) => ({
        title: cat.title,
        entries: cat.entries.filter((e) => e.q.toLowerCase().includes(q) || e.a.toLowerCase().includes(q)),
      }))
      .filter((cat) => cat.entries.length > 0);
  });
}
