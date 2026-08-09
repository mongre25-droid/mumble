export function browserLaunchOptions(requestedExecutablePath) {
  const executablePath = requestedExecutablePath?.trim();
  return executablePath ? { executablePath } : { channel: 'chrome' };
}
