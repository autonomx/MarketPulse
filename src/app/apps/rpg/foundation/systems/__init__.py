"""RPG Systems - Independent subsystems that react to events.

Available systems:
- combat_system: Handles damage and death events (priority -10)
- emotion_system: Handles NPC emotional responses (priority 0)
- scene_system: Records events for narrative generation (priority 5)
- memory_system: Records events into NPC memory (priority 10)
- debug_system: Logs all events for debugging (priority 20, optional)

Priority system:
    Lower priority runs first. All systems register with appropriate
    priorities to ensure deterministic execution order:
        1. Combat (-10): Mutates HP, publishes death
        2. Emotion (0):   Reacts to combat results
        3. Scene (5):     Records for narrative
        4. Memory (10):   Stores in NPC memory
        5. Debug (20):    Logs final state
"""