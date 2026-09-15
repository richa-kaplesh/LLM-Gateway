import * as React from 'react'
import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg text-sm font-medium transition-all duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500/40 focus-visible:ring-offset-1 disabled:pointer-events-none disabled:opacity-40 cursor-pointer active:scale-[0.97]',
  {
    variants: {
      variant: {
        default: 'bg-amber-700 text-white hover:bg-amber-800 shadow-sm hover:shadow',
        outline: 'border border-stone-300 bg-white text-stone-700 hover:bg-stone-50 hover:text-stone-900 hover:border-stone-400 shadow-sm',
        ghost: 'text-stone-600 hover:bg-stone-100 hover:text-stone-900',
        destructive: 'bg-red-50 text-red-700 border border-red-200 hover:bg-red-100',
      },
      size: {
        default: 'h-9 px-4 py-2',
        sm: 'h-8 px-3 text-xs',
        lg: 'h-10 px-6',
        icon: 'h-9 w-9',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  }
)

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : 'button'
    return <Comp className={cn(buttonVariants({ variant, size, className }))} ref={ref} {...props} />
  }
)
Button.displayName = 'Button'

export { Button, buttonVariants }
