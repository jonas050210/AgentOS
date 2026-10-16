import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Badge, ErrorBanner, StatusBadge, Toggle } from './ui';

describe('reusable UI components', () => {
  it('renders a status badge with an accessible label', () => {
    render(<StatusBadge level="warn" label="degraded" />);
    expect(screen.getByText('degraded')).toBeInTheDocument();
  });

  it('renders badges for every status level', () => {
    render(
      <div>
        <Badge tone="ok">ok</Badge>
        <Badge tone="error">error</Badge>
        <Badge tone="unknown">unknown</Badge>
      </div>,
    );
    expect(screen.getByText('ok')).toBeInTheDocument();
    expect(screen.getByText('error')).toBeInTheDocument();
    expect(screen.getByText('unknown')).toBeInTheDocument();
  });

  it('shows an error banner and wires retry/dismiss', async () => {
    const onRetry = vi.fn();
    const onDismiss = vi.fn();
    render(<ErrorBanner message="Backend offline" onRetry={onRetry} onDismiss={onDismiss} />);
    expect(screen.getByRole('alert')).toHaveTextContent('Backend offline');
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await userEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(onRetry).toHaveBeenCalledOnce();
    expect(onDismiss).toHaveBeenCalledOnce();
  });

  it('toggles state through the switch role', async () => {
    const onChange = vi.fn();
    render(<Toggle label="Network access" checked={false} onChange={onChange} />);
    const sw = screen.getByRole('switch', { name: 'Network access' });
    expect(sw).toHaveAttribute('aria-checked', 'false');
    await userEvent.click(sw);
    expect(onChange).toHaveBeenCalledWith(true);
  });
});
