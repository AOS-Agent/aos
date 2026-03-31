import { forwardRef, type InputHTMLAttributes } from "react";

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ label, className = "", id, ...props }, ref) => {
    const inputId = id || label?.toLowerCase().replace(/\s+/g, "-");

    return (
      <div className="flex flex-col">
        {label && (
          <label
            htmlFor={inputId}
            className="text-xs font-medium text-text-tertiary mb-1.5"
          >
            {label}
          </label>
        )}
        <input
          ref={ref}
          id={inputId}
          className={`
            h-9 w-full px-2.5
            rounded-sm border border-border-secondary bg-bg-tertiary
            text-sm text-text placeholder:text-text-quaternary
            transition-colors duration-100
            hover:border-border-tertiary
            focus:outline-none focus:border-accent focus:ring-2 focus:ring-accent/20
            disabled:opacity-40 disabled:pointer-events-none
            ${className}
          `}
          {...props}
        />
      </div>
    );
  }
);

Input.displayName = "Input";
