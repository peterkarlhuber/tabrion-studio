/** STU-008: a pasted `postgresql://` URL fills the profile, or is refused as a whole (never taken in part). */
import { parsePostgresUrl } from './postgresUrl';

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
