 
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';

import {
  recordLiveConversationEvaluationEvent,
  resetLiveConversationEvaluation,
} from '../workspace/live-conversation-evaluation-controller';
import { LiveConversationEvaluationPanel } from './LiveConversationEvaluationPanel';

describe('LiveConversationEvaluationPanel', () => {
  beforeEach(() => {
    window.localStorage.clear();
    resetLiveConversationEvaluation();
  });

  it('renders measured metrics and saves optional experience scores', () => {
    recordLiveConversationEvaluationEvent({ atMs: 1, type: 'first_audio', latencyMs: 720 });
    recordLiveConversationEvaluationEvent({ atMs: 2, type: 'interruption', success: true, latencyMs: 110 });
    render(<LiveConversationEvaluationPanel />);

    expect(screen.getByText('720 ms')).toBeInTheDocument();
    expect(screen.getByText('100%')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Perceived listening score'), { target: { value: '5' } });
    fireEvent.change(screen.getByLabelText('Perceived pressure score'), { target: { value: '1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save experience score' }));

    expect(screen.getByRole('status')).toHaveTextContent('Conversation experience score saved');
    expect(screen.getByText('5 / 1')).toBeInTheDocument();
  });

  it('resets the report without inventing values', () => {
    recordLiveConversationEvaluationEvent({ atMs: 1, type: 'first_audio', latencyMs: 900 });
    render(<LiveConversationEvaluationPanel />);
    fireEvent.click(screen.getByRole('button', { name: 'Reset evaluation' }));

    expect(screen.getByText('0 events')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('evaluation reset');
  });
});
