import './rpgUiState';

declare module './rpgUiState' {
  interface RpgStoryMessagePreview {
    id?: string;
    interactionId?: string;
    submissionId?: string;
    messageKind?: string;
    messageIndex?: number;
  }
}
