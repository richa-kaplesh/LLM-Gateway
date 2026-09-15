import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const badgeVariants = cva(
  'inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium transition-colors',
  {
    variants: {
      variant: {
        default: 'border-stone-200 bg-stone-100 text-stone-700',
        groq: 'border-amber-200 bg-amber-50 text-amber-800',
        gemini: 'border-blue-200 bg-blue-50 text-blue-800',
        tool: 'border-violet-200 bg-violet-50 text-violet-800',
        cached: 'border-green-200 bg-green-50 text-green-800',
        uncached: 'border-stone-200 bg-stone-100 text-stone-500',
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
