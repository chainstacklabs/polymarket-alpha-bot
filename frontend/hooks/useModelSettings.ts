'use client'

import { useState, useEffect } from 'react'

export interface ModelSettings {
  implicationsModel: string
  validationModel: string
}

const STORAGE_KEY = 'alphapoly:pipeline-model-settings'
const DEFAULTS: ModelSettings = { implicationsModel: '', validationModel: '' }

export function useModelSettings() {
  const [settings, setSettings] = useState<ModelSettings>(DEFAULTS)

  useEffect(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY)
      if (stored) setSettings(JSON.parse(stored))
    } catch {}
  }, [])

  function update(patch: Partial<ModelSettings>) {
    setSettings((prev) => {
      const next = { ...prev, ...patch }
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
      return next
    })
  }

  return { settings, update }
}
