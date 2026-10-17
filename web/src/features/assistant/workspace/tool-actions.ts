export type ToolActionCategory = 'read' | 'write' | 'delete' | 'execute';

export type ToolActionRiskLevel = 'low' | 'medium' | 'high';

export type ApprovalPolicy = 'always_ask' | 'ask_sensitive' | 'allow_automatic' | 'disabled';

export type ToolAction = {
  id: string;
  label: string;
  description: string;
  category: ToolActionCategory;
  enabled: boolean;
  approvalPolicy: ApprovalPolicy;
  riskLevel: ToolActionRiskLevel;
  requiresConnection: boolean;
  requiresConfirmation: boolean;
  isDestructive: boolean;
};

export type ToolActionExecutionGate = {
  allowed: boolean;
  approvalRequired: boolean;
  reason?: string;
};

export type ToolActionDefinition = Omit<ToolAction, 'enabled' | 'approvalPolicy'> & {
  enabled?: boolean;
  approvalPolicy?: ApprovalPolicy;
};

export function createToolAction(definition: ToolActionDefinition): ToolAction {
  return {
    ...definition,
    enabled: definition.enabled ?? true,
    approvalPolicy: definition.approvalPolicy ?? defaultApprovalPolicyForAction(definition),
  };
}

export function defaultApprovalPolicyForAction(action: Pick<ToolAction, 'category' | 'riskLevel' | 'isDestructive' | 'requiresConfirmation'>): ApprovalPolicy {
  if (action.isDestructive || action.category === 'delete') {
    return 'always_ask';
  }

  if (action.requiresConfirmation || action.riskLevel === 'high') {
    return 'always_ask';
  }

  if (action.category === 'write' || action.category === 'execute' || action.riskLevel === 'medium') {
    return 'ask_sensitive';
  }

  return 'allow_automatic';
}

export function canExecuteToolAction(action: ToolAction): ToolActionExecutionGate {
  if (!action.enabled) {
    return {
      allowed: false,
      approvalRequired: false,
      reason: 'Tool action is disabled.',
    };
  }

  if (action.approvalPolicy === 'disabled') {
    return {
      allowed: false,
      approvalRequired: false,
      reason: 'Tool action approval policy is disabled.',
    };
  }

  return {
    allowed: true,
    approvalRequired: requiresToolActionApproval(action),
  };
}

export function requiresToolActionApproval(action: ToolAction): boolean {
  if (action.approvalPolicy === 'always_ask') {
    return true;
  }

  if (action.approvalPolicy === 'ask_sensitive') {
    return action.requiresConfirmation || action.isDestructive || action.riskLevel !== 'low' || action.category !== 'read';
  }

  return false;
}

export function updateToolActionEnabled(action: ToolAction, enabled: boolean): ToolAction {
  return { ...action, enabled };
}

export function updateToolActionApprovalPolicy(action: ToolAction, approvalPolicy: ApprovalPolicy): ToolAction {
  return { ...action, approvalPolicy };
}

export const DEFAULT_GMAIL_TOOL_ACTIONS = [
  createToolAction({
    id: 'gmail.read_email',
    label: 'Read email',
    description: 'Search and read Gmail messages and threads.',
    category: 'read',
    riskLevel: 'low',
    requiresConnection: true,
    requiresConfirmation: false,
    isDestructive: false,
  }),
  createToolAction({
    id: 'gmail.read_attachment',
    label: 'Read attachments',
    description: 'Read attachments from selected Gmail messages.',
    category: 'read',
    riskLevel: 'medium',
    requiresConnection: true,
    requiresConfirmation: false,
    isDestructive: false,
  }),
  createToolAction({
    id: 'gmail.create_draft',
    label: 'Create drafts',
    description: 'Create reviewable Gmail drafts without sending.',
    category: 'write',
    riskLevel: 'medium',
    requiresConnection: true,
    requiresConfirmation: false,
    isDestructive: false,
  }),
  createToolAction({
    id: 'gmail.send_email',
    label: 'Send email',
    description: 'Send or reply to Gmail messages.',
    category: 'write',
    riskLevel: 'high',
    requiresConnection: true,
    requiresConfirmation: true,
    isDestructive: false,
  }),
  createToolAction({
    id: 'gmail.delete_email',
    label: 'Delete email',
    description: 'Move Gmail messages to Trash.',
    category: 'delete',
    riskLevel: 'high',
    requiresConnection: true,
    requiresConfirmation: true,
    isDestructive: true,
    enabled: false,
  }),
] as const;

export const DEFAULT_CALENDAR_TOOL_ACTIONS = [
  createToolAction({
    id: 'calendar.read_availability',
    label: 'Read availability',
    description: 'Read calendar availability and event summaries.',
    category: 'read',
    riskLevel: 'low',
    requiresConnection: true,
    requiresConfirmation: false,
    isDestructive: false,
  }),
  createToolAction({
    id: 'calendar.create_event',
    label: 'Create events',
    description: 'Create Google Calendar events.',
    category: 'write',
    riskLevel: 'medium',
    requiresConnection: true,
    requiresConfirmation: true,
    isDestructive: false,
  }),
  createToolAction({
    id: 'calendar.delete_event',
    label: 'Delete events',
    description: 'Delete Google Calendar events.',
    category: 'delete',
    riskLevel: 'high',
    requiresConnection: true,
    requiresConfirmation: true,
    isDestructive: true,
  }),
] as const;

export const DEFAULT_CONTACTS_TOOL_ACTIONS = [
  createToolAction({
    id: 'contacts.search_contacts',
    label: 'Search contacts',
    description: 'Search Google Contacts to resolve people and email addresses.',
    category: 'read',
    riskLevel: 'low',
    requiresConnection: true,
    requiresConfirmation: false,
    isDestructive: false,
  }),
  createToolAction({
    id: 'contacts.resolve_recipient',
    label: 'Resolve recipients',
    description: 'Use contact details to help address emails or calendar invites.',
    category: 'read',
    riskLevel: 'medium',
    requiresConnection: true,
    requiresConfirmation: false,
    isDestructive: false,
  }),
] as const;

export const DEFAULT_GITHUB_TOOL_ACTIONS = [
  createToolAction({
    id: 'github.read_repo',
    label: 'Read repositories',
    description: 'Read repository metadata, files, issues, pull requests, and checks.',
    category: 'read',
    riskLevel: 'low',
    requiresConnection: true,
    requiresConfirmation: false,
    isDestructive: false,
  }),
  createToolAction({
    id: 'github.create_branch',
    label: 'Create branches',
    description: 'Create GitHub branches for implementation work.',
    category: 'write',
    riskLevel: 'medium',
    requiresConnection: true,
    requiresConfirmation: false,
    isDestructive: false,
  }),
  createToolAction({
    id: 'github.create_pr',
    label: 'Open pull requests',
    description: 'Open pull requests from prepared branch changes.',
    category: 'write',
    riskLevel: 'medium',
    requiresConnection: true,
    requiresConfirmation: false,
    isDestructive: false,
  }),
  createToolAction({
    id: 'github.comment_pr',
    label: 'Comment on pull requests',
    description: 'Comment on GitHub pull requests and issues.',
    category: 'write',
    riskLevel: 'medium',
    requiresConnection: true,
    requiresConfirmation: false,
    isDestructive: false,
  }),
  createToolAction({
    id: 'github.merge_pr',
    label: 'Merge pull requests',
    description: 'Merge pull requests after required checks pass.',
    category: 'write',
    riskLevel: 'high',
    requiresConnection: true,
    requiresConfirmation: true,
    isDestructive: false,
  }),
  createToolAction({
    id: 'github.delete_branch',
    label: 'Delete branches',
    description: 'Delete remote GitHub branches.',
    category: 'delete',
    riskLevel: 'high',
    requiresConnection: true,
    requiresConfirmation: true,
    isDestructive: true,
    enabled: false,
  }),
] as const;
