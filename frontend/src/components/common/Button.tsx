import React from 'react';
import { Loader2 } from 'lucide-react';

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'outline' | 'ghost' | 'danger';
  size?: 'sm' | 'md' | 'lg';
  isLoading?: boolean;
  leftIcon?: React.ReactNode;
  rightIcon?: React.ReactNode;
}

export const Button: React.FC<ButtonProps> = ({
  children,
  variant = 'primary',
  size = 'md',
  isLoading = false,
  leftIcon,
  rightIcon,
  className = '',
  disabled,
  ...props
}) => {
  const variantStyles = {
    primary:
      'bg-primary text-white hover:bg-primary-hover shadow-sm active:scale-[0.98] border border-transparent',
    secondary:
      'bg-white text-slate-700 hover:bg-slate-50 border border-slate-200 shadow-sm active:scale-[0.98]',
    outline:
      'bg-transparent text-primary hover:bg-primary-light border border-primary/40 active:scale-[0.98]',
    ghost:
      'bg-transparent text-slate-600 hover:bg-slate-100 hover:text-slate-900 active:scale-[0.98]',
    danger:
      'bg-red-600 text-white hover:bg-red-700 shadow-sm active:scale-[0.98] border border-transparent',
  };

  const sizeStyles = {
    sm: 'text-xs px-3 py-1.5 rounded-md gap-1.5 font-medium',
    md: 'text-sm px-4 py-2 rounded-lg gap-2 font-medium',
    lg: 'text-base px-5 py-2.5 rounded-xl gap-2.5 font-semibold',
  };

  return (
    <button
      disabled={disabled || isLoading}
      className={`inline-flex items-center justify-center transition-all duration-150 focus:outline-none focus:ring-2 focus:ring-primary/20 focus:ring-offset-1 disabled:opacity-50 disabled:pointer-events-none ${variantStyles[variant]} ${sizeStyles[size]} ${className}`}
      {...props}
    >
      {isLoading ? (
        <Loader2 className="w-4 h-4 animate-spin text-current" />
      ) : (
        leftIcon && <span className="flex-shrink-0">{leftIcon}</span>
      )}
      <span>{children}</span>
      {!isLoading && rightIcon && <span className="flex-shrink-0">{rightIcon}</span>}
    </button>
  );
};
