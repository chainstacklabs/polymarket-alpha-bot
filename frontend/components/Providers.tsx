'use client'

import { ReactNode } from 'react'
import { WalletProvider } from '@/hooks/useWallet'

export function Providers({ children }: { children: ReactNode }) {
  return <WalletProvider>{children}</WalletProvider>
}
