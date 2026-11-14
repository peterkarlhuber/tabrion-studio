/**
 * The cases of the QA-S1 checklist (QA-S1-Checkliste.md, the owner's manual usability pass of Studio 1.0) and how far a
 * browser test can judge each. The suite has one test per case, titled with its id; a case marked `partly` leaves
 * the part named in `manual` to the manual pass. The questions of "Gesamteindruck" (overall impression) have no test:
 * they ask for an opinion, and the results file lists them as not automated with the reason given here.
 *
 * QA-S1 acceptance stays with the manual pass; this suite only catches regressions before it.
 */
export type Automation = 'full' | 'partly';

export interface Case {
  id: string;
  section: string;
  automation: Automation;
  /** What stays for the manual pass (partly automated cases). */
  manual?: string;
}

export const CASES: Case[] = [
  {
    id: 'S1-01',
    section: 'A',
    automation: 'partly',
    manual:
      'Double-clicking the kit script, the terminal window staying open and the system browser opening by itself are operating-system behavior.',
  },
  {
    id: 'S1-02',
    section: 'A',
    automation: 'partly',
    manual: 'Whether the dialog is understood.',
  },
  {
    id: 'S1-03',
    section: 'A',
    automation: 'partly',
    manual: 'Whether a first-time user finds the way unaided in under 60 seconds.',
  },
  { id: 'S1-04', section: 'A', automation: 'full' },
  { id: 'S1-05', section: 'B', automation: 'full' },
  { id: 'S1-06', section: 'B', automation: 'full' },
  { id: 'S1-07', section: 'B', automation: 'full' },
  { id: 'S1-08', section: 'B', automation: 'full' },
  { id: 'S1-09', section: 'B', automation: 'full' },
  { id: 'S1-10', section: 'C', automation: 'full' },
  { id: 'S1-11', section: 'C', automation: 'full' },
  { id: 'S1-12', section: 'C', automation: 'full' },
  { id: 'S1-13', section: 'C', automation: 'full' },
  {
    id: 'S1-14',
    section: 'C',
    automation: 'partly',
    manual: 'Whether the refusal message is understandable.',
  },
  { id: 'S1-15', section: 'C', automation: 'full' },
  { id: 'S1-16', section: 'C', automation: 'full' },
  { id: 'S1-17', section: 'D', automation: 'full' },
  { id: 'S1-18', section: 'D', automation: 'full' },
  { id: 'S1-19', section: 'D', automation: 'full' },
  {
    id: 'S1-20',
    section: 'D',
    automation: 'partly',
    manual: 'Opening the file in Excel or LibreOffice.',
  },
  { id: 'S1-21', section: 'E', automation: 'full' },
  {
    id: 'S1-22',
    section: 'E',
    automation: 'partly',
    manual: 'Whether it is immediately clear that the page is on PROD.',
  },
  { id: 'S1-23', section: 'E', automation: 'full' },
  { id: 'S1-24', section: 'E', automation: 'full' },
  { id: 'S1-25', section: 'F', automation: 'full' },
  { id: 'S1-26', section: 'F', automation: 'full' },
  {
    id: 'S1-27',
    section: 'F',
    automation: 'partly',
    manual:
      'Pasting into a text editor of the operating system (the test reads the clipboard in the browser).',
  },
  { id: 'S1-28', section: 'G', automation: 'full' },
  {
    id: 'S1-29',
    section: 'G',
    automation: 'partly',
    manual: 'Opening the report in a text editor.',
  },
  // Checklist v2: the vector data view (S1-35 to S1-37, section G) and the old tab after a restart (S1-38, J).
  { id: 'S1-35', section: 'G', automation: 'full' },
  {
    id: 'S1-36',
    section: 'G',
    automation: 'partly',
    manual: 'Whether the terms are understandable.',
  },
  { id: 'S1-37', section: 'G', automation: 'full' },
  {
    id: 'S1-30',
    section: 'H',
    automation: 'partly',
    manual:
      'Wording on the German pages; the test finds only English catalog phrases and raw keys.',
  },
  {
    id: 'S1-31',
    section: 'H',
    automation: 'partly',
    manual: 'Readability is judged by eye; the test checks color contrast with axe.',
  },
  { id: 'S1-32', section: 'I', automation: 'full' },
  {
    id: 'S1-33',
    section: 'J',
    automation: 'partly',
    manual:
      'Double-clicking the kit script; the test runs the command the script runs (tabrion-studio --stop).',
  },
  {
    id: 'S1-34',
    section: 'J',
    automation: 'partly',
    manual:
      'Double-clicking the kit scripts; the test starts and stops Studio with their commands.',
  },
  {
    id: 'S1-38',
    section: 'J',
    automation: 'partly',
    manual:
      'Double-clicking the kit scripts and the browser’s own error page; on macOS the address may stay 127.0.0.1 with another port.',
  },
];

/** The questions of "Gesamteindruck": opinions, so never automated. */
export const NOT_AUTOMATED: { id: string; reason: string }[] = [
  { id: 'G-1', reason: 'How easy it was to find the way (1 to 5) is an opinion.' },
  { id: 'G-2', reason: 'The time to the first table is the tester’s own time (S1-03).' },
  { id: 'G-3', reason: 'Whether DEV and PROD were always clear is a perception.' },
  { id: 'G-4', reason: 'Whether messages and dialogs were understandable is a perception.' },
  { id: 'G-5', reason: 'What the tester liked best is an opinion.' },
  { id: 'G-6', reason: 'What disturbed or confused the tester is an opinion.' },
  { id: 'G-7', reason: 'Wording that sounds wrong or clumsy needs a reader.' },
  { id: 'G-8', reason: 'The release decision belongs to the tester.' },
  { id: 'G-9', reason: 'Free comments.' },
];
