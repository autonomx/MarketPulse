import { describe, expect, it } from 'vitest';
import { buildProfileInput, cloneFormDefaults, type CloneModuleDefaults } from './cloneFormDefaults';

const defaults: CloneModuleDefaults = {
  providerId: 'qwen-voice',
  language: 'French',
  quality: 'Standard',
};

describe('profile form defaults', () => {
  it('initializes the form from central defaults', () => {
    expect(cloneFormDefaults(defaults)).toMatchObject({
      providerId: 'qwen-voice',
      language: 'French',
      quality: 'Standard',
      profileName: '',
    });
  });

  it('uses central values when the job form has no override', () => {
    expect(buildProfileInput({ ...cloneFormDefaults(defaults), providerId: '', language: '', quality: '', profileName: 'Narrator' }, defaults)).toMatchObject({
      provider_id: 'qwen-voice',
      language: 'French',
      quality: 'Standard',
      profile_name: 'Narrator',
    });
  });

  it('preserves explicit job overrides', () => {
    expect(buildProfileInput({ ...cloneFormDefaults(defaults), providerId: 'remote-voice', language: 'Spanish', quality: 'Draft', profileName: 'Guide' }, defaults)).toMatchObject({
      provider_id: 'remote-voice',
      language: 'Spanish',
      quality: 'Draft',
    });
  });
});
