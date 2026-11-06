export interface ResearchProgress {
  version: "research-progress-v1";
  startedAt: string;
  lastEventAt: string | null;
  elapsedMs: number;
  phase:
    | "MODEL_RUNNING"
    | "WAITING_TOOL"
    | "MODEL_MESSAGE"
    | "TURN_COMPLETED"
    | "TURN_FAILED";
  completedToolActions: number | null;
  activeToolActions: number | null;
  waitingExternalTools: boolean | null;
  modelCompleted: boolean;
  observedSearchQueries?: number | null;
  observedPageOpens?: number | null;
  observedPageFinds?: number | null;
}

export interface ResearchExecutionStatistics {
  version: "research-execution-stats-v1";
  coverage: "COMPLETE" | "PARTIAL" | "UNAVAILABLE";
  completedToolActions: number | null;
  distinctSearchQueries: number | null;
  duplicateSearchQueries: number | null;
  distinctPageTargets: number | null;
  repeatedPageOpens: number | null;
  sourceReferenceReuses: null;
  preDispatchEnforcement: false;
  providerRetries: null;
}
