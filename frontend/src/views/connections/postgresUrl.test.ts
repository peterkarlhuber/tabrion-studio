/** STU-008: a pasted `postgresql://` URL fills the profile, or is refused as a whole (never taken in part). */
import { parsePostgresUrl, withoutPassword } from './postgresUrl';

describe('parsePostgresUrl', () => {
  it('takes host, port, database, user, password and sslmode, decoded', () => {
    expect(
      parsePostgresUrl(
        ' postgresql://studio%40ro:p%3Aw%2Fd@DB.Internal:80/my%20db?sslmode=verify-ca ',
      ),
    ).toEqual({
      host: 'DB.Internal',
      port: '80',
      database: 'my db',
      user: 'studio@ro',
      password: 'p:w/d',
      sslmode: 'verify-ca',
    });
    expect(parsePostgresUrl('POSTGRES://[::1]:5433/d')).toEqual({
      host: '::1',
      port: '5433',
      database: 'd',
      user: '',
      password: '',
      sslmode: '',
    });
    expect(parsePostgresUrl('postgresql://h')).toMatchObject({ host: 'h', database: '' });
  });

  it.each([
    ['mysql://h/d', 'scheme'],
    ['h/d', 'scheme'],
    ['postgresql://h:99999/d', 'invalid'],
    ['postgresql://h/d#x', 'invalid'],
    ['postgresql://h/a%2Fb', 'invalid'],
    ['postgresql://h/%E0%A4%A', 'invalid'],
    ['postgresql:///d?host=/tmp', 'host'],
    ['postgresql://h1,h2/d', 'host'],
    ['postgresql://h%2Fx/d', 'host'],
    ['postgresql://h/d?sslmode=require&options=-c%20role%3Dadmin', 'parameters'],
    ['postgresql://h/d?sslrootcert=/etc/ca.pem', 'parameters'],
    ['postgresql://h/d?sslmode=sometimes', 'parameters'],
    ['postgresql://h/d?sslmode=require&sslmode=disable', 'parameters'],
  ])('refuses %s (%s)', (url, refusal) => {
    expect(parsePostgresUrl(url)).toBe(refusal);
  });
});

describe('withoutPassword (L-2 of the connect review)', () => {
  it.each([
    ['postgresql://ro:pw@h1,h2/m', 'postgresql://ro@h1,h2/m'],
    ['postgres://ro:p@ss@h/m?x=1', 'postgres://ro@h/m?x=1'],
    ['postgresql://:pw@h/m', 'postgresql://@h/m'],
    // A password with a slash: the userinfo cannot be told apart, so nothing of the text stays.
    ['postgresql://ro:p/w@h/m', ''],
    ['postgresql://ro@h/m', 'postgresql://ro@h/m'],
    ['postgresql://h:5432/m', 'postgresql://h:5432/m'],
    ['mysql://h/d', 'mysql://h/d'],
    // The delta review's residue: a password given as a query parameter.
    ['postgresql://ro@h/m?password=pw', 'postgresql://ro@h/m'],
    ['postgresql://h/m?sslmode=require&password=pw&x=1', 'postgresql://h/m?sslmode=require&x=1'],
    ['postgresql://h/m?%70assword=pw', 'postgresql://h/m'],
    ['postgresql://h/m?PASSWORD=pw&password=two', 'postgresql://h/m'],
    ['postgresql://ro:pw@h/m?password=pw2&passfile=x', 'postgresql://ro@h/m?passfile=x'],
    ['postgresql://h/m?passwords=x', 'postgresql://h/m?passwords=x'],
    // libpq's passphrase of the client key is a secret too (#44 review).
    [
      'postgresql://h/m?sslmode=verify-full&sslpassword=key-pw',
      'postgresql://h/m?sslmode=verify-full',
    ],
    ['postgresql://h/m?SSLPassword=key-pw&ssl%70assword=x', 'postgresql://h/m'],
    // URL-encoded form: '+' is a space, so 'password+' is the name 'password'.
    ['postgresql://h/m?password+=x', 'postgresql://h/m'],
  ])('%s -> %s', (text, expected) => {
    expect(withoutPassword(text)).toBe(expected);
  });
});
