import { useState } from 'react'
import { Clapperboard, LayoutDashboard, Star, Video, CheckCircle } from 'lucide-react'
import { cn } from '@/lib/utils'
import { DashboardPage } from '@/pages/DashboardPage'
import { PriorityNewsPage } from '@/pages/PriorityNewsPage'
import { WeeklyDraftPage } from '@/pages/WeeklyDraftPage'
import { UsedNewsPage } from '@/pages/UsedNewsPage'

type Tab = 'dashboard' | 'priority' | 'draft' | 'used'

const TABS = [
  { id: 'dashboard' as Tab, label: 'ダッシュボード', icon: LayoutDashboard },
  { id: 'priority' as Tab, label: '優先度Aニュース', icon: Star },
  { id: 'draft' as Tab, label: '週次ドラフト', icon: Video },
  { id: 'used' as Tab, label: '使用済み', icon: CheckCircle },
]

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>('dashboard')

  return (
    <div className="flex flex-col h-screen bg-background">
      <header className="flex items-center gap-2 px-4 h-12 border-b bg-white shrink-0 shadow-sm">
        <Clapperboard className="w-5 h-5 text-primary" />
        <h1 className="font-bold text-base tracking-tight">AI News Studio</h1>
        <span className="text-xs text-muted-foreground ml-1">/ 動画企画管理</span>
      </header>

      <nav className="flex border-b bg-white shrink-0 px-4 gap-1 overflow-x-auto">
        {TABS.map(tab => {
          const active = activeTab === tab.id
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={cn(
                'flex items-center gap-1.5 px-3 py-2.5 text-sm whitespace-nowrap border-b-2 transition-colors',
                active
                  ? 'border-primary text-primary font-medium'
                  : 'border-transparent text-muted-foreground hover:text-foreground hover:border-muted-foreground/30'
              )}
            >
              <tab.icon className="w-3.5 h-3.5" />
              {tab.label}
            </button>
          )
        })}
      </nav>

      <main className="flex-1 overflow-hidden">
        {activeTab === 'dashboard' && <DashboardPage />}
        {activeTab === 'priority' && <PriorityNewsPage />}
        {activeTab === 'draft' && <WeeklyDraftPage />}
        {activeTab === 'used' && <UsedNewsPage />}
      </main>
    </div>
  )
}
