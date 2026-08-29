// Vitest test setup -- "Frontend Component Test Suite" sprint
// (2026-08-29), the first test infrastructure this app has had.
//
// Importing the `/vitest` subpath (not the bare package) both runs the
// matcher registration AND ambient-augments vitest's own `expect`
// interface with the jest-dom matcher types (toBeInTheDocument(),
// etc.) -- since this file is included in the same tsconfig.app.json
// program as every test file (both live under src/), that augmentation
// applies across all of them without any tsconfig.app.json changes.
import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// React Testing Library doesn't auto-cleanup outside Jest's global
// afterEach hook -- without this, a component mounted in one test stays
// in jsdom's document for the next one, which would make "query by
// text" assertions in a later test silently match a PREVIOUS test's
// leftover DOM instead of failing honestly.
afterEach(() => {
  cleanup()
})
