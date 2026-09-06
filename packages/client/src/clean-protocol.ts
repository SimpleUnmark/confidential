export type CleanSuccess = {
  ok: true;
  requestId: string | null;
  cleanedText: string;
  words: number;
  characters: number;
  creditsUsed: number;
  remainingCredits: number | null;
  guestCleansLeft: number | null;
  removed: number;
  normalizedSpaces: number;
  source: "watermarks-remover";
  strongApplied: boolean;
  warning: string | null;
  privacyMode: "PRIVATE" | "CONFIDENTIAL";
};

export type CleanFailure = {
  ok: false;
  error: string;
  code: "INVALID" | "LIMIT" | "AUTH" | "CREDITS" | "RATE_LIMIT" | "SERVICE";
  remainingCredits?: number | null;
  guestCleansLeft?: number | null;
};

export type CleanActionResult = CleanSuccess | CleanFailure;

export type CleanAuthorizationSuccess = {
  ok: true;
  requestId: string;
  authorizationToken: string;
  confidentialServerUrl: string;
  remainingCredits: number | null;
  guestCleansLeft: number | null;
};

export type CleanAuthorizationResult = CleanAuthorizationSuccess | CleanFailure;

export type CleanStreamEvent =
  | {
      type: "started";
      requestId: string | null;
      remainingCredits: number | null;
      guestCleansLeft: number | null;
    }
  | {
      type: "phase";
      phase: "cleaning" | "rephrasing";
    }
  | {
      type: "delta";
      text: string;
    }
  | {
      type: "complete";
      result: CleanSuccess;
    }
  | {
      type: "error";
      error: CleanFailure;
    };
