import type { CSSProperties } from 'react'

export type IconName =
  | 'activity'
  | 'scenario'
  | 'environment'
  | 'play'
  | 'live'
  | 'stop'
  | 'delete'
  | 'edit'
  | 'download'
  | 'copy'
  | 'close'
  | 'collapseLeft'
  | 'collapseRight'
  | 'chevronDown'
  | 'chevronUp'
  | 'message'
  | 'lock'
  | 'world'
  | 'warning'
  | 'prompt'
  | 'theme'
  | 'phone'
  | 'mail'
  | 'document'
  | 'chat'
  | 'face'
  | 'open'

interface Props {
  name: IconName
  size?: number
  strokeWidth?: number
  style?: CSSProperties
  className?: string
}

export function Icon({ name, size = 16, strokeWidth = 1.8, style, className }: Props) {
  const common = {
    width: size,
    height: size,
    viewBox: '0 0 24 24',
    fill: 'none',
    xmlns: 'http://www.w3.org/2000/svg',
    'aria-hidden': true,
    className,
    style,
  } as const

  const strokeProps = {
    stroke: 'currentColor',
    strokeWidth,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
  }

  switch (name) {
    case 'activity':
      return (
        <svg {...common}>
          <path {...strokeProps} d="M4 13h3l2-6 4 12 2-6h5" />
        </svg>
      )
    case 'scenario':
      return (
        <svg {...common}>
          <path {...strokeProps} d="M6 4h8l4 4v12H6z" />
          <path {...strokeProps} d="M14 4v4h4" />
          <path {...strokeProps} d="M9 12h6M9 16h6" />
        </svg>
      )
    case 'environment':
      return (
        <svg {...common}>
          <path {...strokeProps} d="M12 3c4.97 0 9 4.03 9 9s-4.03 9-9 9-9-4.03-9-9 4.03-9 9-9Z" />
          <path {...strokeProps} d="M3 12h18M12 3a15 15 0 0 1 0 18M12 3a15 15 0 0 0 0 18" />
        </svg>
      )
    case 'play':
      return (
        <svg {...common}>
          <path {...strokeProps} d="M8 6.5v11l8-5.5z" />
        </svg>
      )
    case 'live':
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="3" fill="currentColor" />
          <circle {...strokeProps} cx="12" cy="12" r="7" />
        </svg>
      )
    case 'stop':
      return (
        <svg {...common}>
          <rect {...strokeProps} x="7" y="7" width="10" height="10" />
        </svg>
      )
    case 'delete':
      return (
        <svg {...common}>
          <path {...strokeProps} d="M5 7h14M9 7V5h6v2M8 7l1 12h6l1-12" />
        </svg>
      )
    case 'edit':
      return (
        <svg {...common}>
          <path {...strokeProps} d="M4 20h4l10-10-4-4L4 16z" />
          <path {...strokeProps} d="M12 6l4 4" />
        </svg>
      )
    case 'download':
      return (
        <svg {...common}>
          <path {...strokeProps} d="M12 4v10" />
          <path {...strokeProps} d="m8 10 4 4 4-4" />
          <path {...strokeProps} d="M5 20h14" />
        </svg>
      )
    case 'copy':
      return (
        <svg {...common}>
          <rect {...strokeProps} x="9" y="9" width="10" height="10" rx="1.5" />
          <path {...strokeProps} d="M7 15H6a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h9a1 1 0 0 1 1 1v1" />
        </svg>
      )
    case 'close':
      return (
        <svg {...common}>
          <path {...strokeProps} d="M6 6l12 12M18 6 6 18" />
        </svg>
      )
    case 'collapseLeft':
      return (
        <svg {...common}>
          <path {...strokeProps} d="M15 6l-6 6 6 6" />
        </svg>
      )
    case 'collapseRight':
      return (
        <svg {...common}>
          <path {...strokeProps} d="m9 6 6 6-6 6" />
        </svg>
      )
    case 'chevronDown':
      return (
        <svg {...common}>
          <path {...strokeProps} d="m6 9 6 6 6-6" />
        </svg>
      )
    case 'chevronUp':
      return (
        <svg {...common}>
          <path {...strokeProps} d="m6 15 6-6 6 6" />
        </svg>
      )
    case 'message':
      return (
        <svg {...common}>
          <path {...strokeProps} d="M5 6h14v10H9l-4 3z" />
        </svg>
      )
    case 'lock':
      return (
        <svg {...common}>
          <rect {...strokeProps} x="6" y="10" width="12" height="10" rx="2" />
          <path {...strokeProps} d="M9 10V8a3 3 0 1 1 6 0v2" />
        </svg>
      )
    case 'world':
      return (
        <svg {...common}>
          <path {...strokeProps} d="M12 3 4 7v5c0 5 3.4 7.7 8 9 4.6-1.3 8-4 8-9V7z" />
          <path {...strokeProps} d="M9.5 12h5" />
        </svg>
      )
    case 'warning':
      return (
        <svg {...common}>
          <path {...strokeProps} d="M12 4 3 20h18z" />
          <path {...strokeProps} d="M12 9v4M12 17h.01" />
        </svg>
      )
    case 'prompt':
      return (
        <svg {...common}>
          <path {...strokeProps} d="M8 8 5 12l3 4M16 8l3 4-3 4M13 6l-2 12" />
        </svg>
      )
    case 'theme':
      return (
        <svg {...common}>
          <path {...strokeProps} d="M12 3a9 9 0 1 0 9 9A7 7 0 0 1 12 3Z" />
        </svg>
      )
    case 'phone':
      return (
        <svg {...common}>
          <path {...strokeProps} d="M7 4h3l2 5-2 1a12 12 0 0 0 4 4l1-2 5 2v3c0 1-1 2-2 2A16 16 0 0 1 5 6c0-1 1-2 2-2Z" />
        </svg>
      )
    case 'mail':
      return (
        <svg {...common}>
          <rect {...strokeProps} x="4" y="6" width="16" height="12" rx="2" />
          <path {...strokeProps} d="m5 8 7 5 7-5" />
        </svg>
      )
    case 'document':
      return (
        <svg {...common}>
          <path {...strokeProps} d="M7 3h7l5 5v13H7z" />
          <path {...strokeProps} d="M14 3v5h5M9 13h6M9 17h6" />
        </svg>
      )
    case 'chat':
      return (
        <svg {...common}>
          <path {...strokeProps} d="M4 6h16v10H9l-5 4z" />
        </svg>
      )
    case 'face':
      return (
        <svg {...common}>
          <circle {...strokeProps} cx="12" cy="12" r="9" />
          <path {...strokeProps} d="M9 10h.01M15 10h.01M8.5 15a5 5 0 0 0 7 0" />
        </svg>
      )
    case 'open':
      return (
        <svg {...common}>
          <path {...strokeProps} d="M14 5h5v5M10 14 19 5" />
          <path {...strokeProps} d="M19 13v5H5V5h5" />
        </svg>
      )
    default:
      return null
  }
}
