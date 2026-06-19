import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const badgeVariants = cva(
  'inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium transition-colors',
  {
    variants: {
      variant: {
        default: 'border-[#333] bg-[#1a1a1a] text-[#ededed]',
        groq: 'border-violet-500/30 bg-violet-500/10 text-violet-300',
        gemini: 'border-blue-500/30 bg-blue-500/10 text-blue-300',
        simple: 'border-green-500/30 bg-green-500/10 text-green-300',
        complex: 'border-orange-500/30 bg-orange-500/10 text-orange-300',
        cached: 'border-green-500/30 bg-green-500/10 text-green-300',
        uncached: 'border-[#333] bg-[#1a1a1a] text-[#888]',
      },
    },
    defaultVariants: {
      variant: 'default',
    },
  }
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />
}

export { Badge, badgeVariants }
