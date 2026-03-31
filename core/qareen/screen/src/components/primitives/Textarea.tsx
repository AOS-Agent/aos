import { forwardRef, type TextareaHTMLAttributes } from "react";

interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string;
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ label, className = "", id, ...props }, ref) => {
    const textareaId = id || label?.toLowerCase().replace(/\s+/g, "-");

    return (
      <div className="flex flex-col">
        {label && (
          <label
            htmlFor={textareaId}
            className="text-xs font-medium text-text-tertiary mb-1.5"
          >
            {label}
          </label>
        )}
        <textarea
          ref={ref}
          id={textareaId}
          className={`
            w-full px-2.5 py-2
            rounded-sm border border-border-secondary bg-bg-tertiary
            text-sm text-text placeholder:text-text-quaternary
            transition-colors duration-100
            hover:border-border-tertiary
            focus:outline-none focus:border-accent focus:ring-2 focus:ring-accent/20
            disabled:opacity-40 disabled:pointer-events-none
            resize-y min-h-[72px]
            ${className}
          `}
          {...props}
        />
      </div>
    );
  }
);

Textarea.displayName = "Textarea";
